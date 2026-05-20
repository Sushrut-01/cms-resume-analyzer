from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    Depends,
    HTTPException,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session
from datetime import datetime
import os
import shutil
import json

from database import get_db
from models.candidate import Candidate
from models.client_requirement import ClientRequirement
from models.resume_version import ResumeVersion

from services.pdf_service import extract_text_from_pdf
from services.llm_service import (
    analyze_resume,
    generate_aligned_resume,
    check_ollama_status,
    get_provider_status,
    detect_function,
    check_role_compatibility,
)
from services.docx_service import generate_word_resume, generate_word_from_structured


router = APIRouter(prefix="/candidates", tags=["Candidates"])

UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "./uploads")


# ── GET all candidates ────────────────────────────────────────────────────────
@router.get("/")
def get_all_candidates(db: Session = Depends(get_db)):
    candidates = (
        db.query(Candidate)
        .order_by(Candidate.created_at.desc())
        .all()
    )
    return {"success": True, "data": [to_dict(c) for c in candidates]}


# ── GET single candidate ──────────────────────────────────────────────────────
@router.get("/{candidate_id}")
def get_candidate(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    return {"success": True, "data": to_dict(c)}


# ── UPLOAD resume ─────────────────────────────────────────────────────────────
@router.post("/upload")
async def upload_resume(
    file: UploadFile = File(...),
    client_requirement_id: int = Form(...),
    recruiter_name: str = Form(""),
    db: Session = Depends(get_db),
):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    client_req = (
        db.query(ClientRequirement)
        .filter(
            ClientRequirement.id == client_requirement_id,
            ClientRequirement.active == True,
        )
        .first()
    )

    if not client_req:
        raise HTTPException(
            status_code=400,
            detail="Invalid or inactive client requirement",
        )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file.filename}"
    pdf_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extracted = extract_text_from_pdf(pdf_path)
    if not extracted.get("success"):
        raise HTTPException(
            status_code=400,
            detail=f"PDF extraction failed: {extracted.get('error')}",
        )

    candidate = Candidate(
        name=extracted.get("name"),
        email=extracted.get("email"),
        mobile=extracted.get("mobile"),
        location=extracted.get("location", ""),
        linkedin=extracted.get("linkedin", ""),
        company_name=client_req.client_name,
        role=client_req.job_title,
        recruiter_name=recruiter_name or "",
        client_requirement=client_req,
        original_resume_text=extracted.get("full_text", ""),
        improved_resume_text="",
        manual_resume_text="",
        pdf_path=pdf_path,
        status="Uploaded",
        resume_version_type="ORIGINAL",
        created_at=datetime.utcnow(),
    )

    db.add(candidate)
    db.commit()
    db.refresh(candidate)

    # Store ORIGINAL version
    db.add(
        ResumeVersion(
            candidate_id=candidate.id,
            version_type="ORIGINAL",
            content=candidate.original_resume_text,
            linked_jd_id=client_req.id,
        )
    )
    db.commit()

    return {
        "success": True,
        "candidate_id": candidate.id,
        "extracted": {
            "name": extracted.get("name"),
            "email": extracted.get("email"),
            "mobile": extracted.get("mobile"),
            "location": extracted.get("location", ""),
            "summary": extracted.get("summary"),
        },
    }


# ── ANALYZE with AI (JD enforced) ─────────────────────────────────────────────
@router.post("/{candidate_id}/analyze")
def analyze_candidate(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not c.client_requirement:
        raise HTTPException(
            status_code=400,
            detail="Client requirement (JD) is not selected",
        )

    if not c.client_requirement.active:
        raise HTTPException(
            status_code=400,
            detail="Client requirement is not active",
        )

    if len(c.client_requirement.jd_text.strip()) < 30:
        raise HTTPException(
            status_code=400,
            detail="Client requirement text is too short",
        )

    if not check_ollama_status():
        raise HTTPException(
            status_code=503,
            detail="Ollama LLM is not running",
        )

    result = analyze_resume(
        resume_text=c.original_resume_text,
        jd_text=c.client_requirement.jd_text,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "AI analysis failed"),
        )

    data = result["data"]

    c.score = data.get("score", 0)
    c.match_level = data.get("match_level", "")
    c.improved_resume_text = data.get("final_resume", "")
    c.recommendations = json.dumps(data.get("recommendations", []))
    c.gaps = json.dumps(data.get("gaps", []))
    c.strengths = json.dumps(data.get("strengths", []))
    c.must_have_missing = json.dumps(data.get("must_have_missing", []))
    c.nice_to_have_missing = json.dumps(data.get("nice_to_have_missing", []))
    c.project_suggestions = json.dumps(data.get("project_suggestions", []))
    c.detected_domain = data.get("detected_domain", "")
    c.injection_supported = 1 if data.get("injection_supported", True) else 0
    c.soft_gaps = json.dumps(data.get("soft_gaps", []))
    c.semantic_score = data.get("semantic_score")
    c.candidate_function = data.get("candidate_function", "")
    c.jd_function        = data.get("jd_function", "")
    compat = data.get("role_compatibility", {})
    c.role_compatibility = compat.get("verdict", "") if isinstance(compat, dict) else ""
    c.status = "Bot Analyzed"
    c.resume_version_type = "ANALYZED"
    c.updated_at = datetime.utcnow()

    db.add(
        ResumeVersion(
            candidate_id=c.id,
            version_type="ANALYZED",
            content=c.improved_resume_text,
            linked_jd_id=c.client_requirement.id,
        )
    )

    db.commit()
    db.refresh(c)

    return {"success": True, "data": data}


# ── JD‑Aligned Resume Draft ───────────────────────────────────────────────────
@router.post("/{candidate_id}/generate-jd-aligned-resume")
def generate_jd_aligned(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not c.client_requirement:
        raise HTTPException(
            status_code=400,
            detail="Client requirement (JD) is not selected",
        )

    if not check_ollama_status():
        raise HTTPException(status_code=503, detail="Ollama LLM is not running")

    result = generate_aligned_resume(
        resume_text=c.original_resume_text,
        jd_text=c.client_requirement.jd_text,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "JD alignment failed"),
        )

    draft = result["draft"]
    score_before      = result.get("score_before", 0)
    score_after       = result.get("score_after",  0)
    added_skills      = result.get("added_skills", 0)
    added_projects    = result.get("added_projects", 0)
    sem_score_before  = result.get("sem_score_before")
    sem_score_after   = result.get("sem_score_after")
    rephrased_count   = result.get("rephrased_count", 0)
    soft_gap_count    = result.get("soft_gap_count", 0)

    # Append this run to the candidate's alignment history
    history = _safe_json(c.align_history)
    history.append({
        "run":              len(history) + 1,
        "timestamp":        datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "score_before":     score_before,
        "score_after":      score_after,
        "added_skills":     added_skills,
        "added_projects":   added_projects,
        "sem_score_before": sem_score_before,
        "sem_score_after":  sem_score_after,
        "rephrased_count":  rephrased_count,
        "soft_gap_count":   soft_gap_count,
    })
    c.align_history = json.dumps(history)
    c.resume_version_type = "JD_ALIGNED"
    c.updated_at = datetime.utcnow()
    if result.get("structured_resume_json"):
        c.structured_resume_json = result["structured_resume_json"]

    db.add(
        ResumeVersion(
            candidate_id=c.id,
            version_type="JD_ALIGNED",
            content=draft,
            linked_jd_id=c.client_requirement.id,
        )
    )
    db.commit()

    return {
        "success":          True,
        "draft_resume":     draft,
        "added_skills":     added_skills,
        "added_projects":   added_projects,
        "score_before":     score_before,
        "score_after":      score_after,
        "sem_score_before": sem_score_before,
        "sem_score_after":  sem_score_after,
        "rephrased_count":  rephrased_count,
        "soft_gap_count":   soft_gap_count,
        "align_history":    history,
    }


# ── DOWNLOAD resume ───────────────────────────────────────────────────────────
@router.get("/{candidate_id}/download-resume")
def download_resume(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Use structured JSON when available (Phase B — Groq/Gemini/Azure runs)
    # Falls back to text-parsing path for Ollama or older records
    structured_json = getattr(c, "structured_resume_json", None)
    if structured_json:
        try:
            structured_data = json.loads(structured_json)
            docx_bytes = generate_word_from_structured(structured_data)
        except Exception:
            structured_data = None
            docx_bytes = None
    else:
        structured_data = None
        docx_bytes = None

    if docx_bytes is None:
        resume_text = (
            c.manual_resume_text
            or c.improved_resume_text
            or c.original_resume_text
        )
        docx_bytes = generate_word_resume(
            resume_text=resume_text,
            candidate_name=c.name or "",
            version_label=c.resume_version_type or "ORIGINAL",
        )

    filename = f"{(c.name or 'candidate').replace(' ', '_')}_resume.docx"

    return Response(
        content=docx_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ── Approve candidate ─────────────────────────────────────────────────────────
@router.put("/{candidate_id}/approve")
def approve_candidate(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.status = "Approved"
    db.commit()
    return {"success": True, "status": "Approved"}


# ── Update improved resume text ───────────────────────────────────────────────
@router.put("/{candidate_id}/update")
async def update_resume(
    candidate_id: int,
    improved_text: str = Form(...),
    db: Session = Depends(get_db),
):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.improved_resume_text = improved_text
    db.commit()
    return {"success": True}


# ── Save manual / JD-aligned resume ──────────────────────────────────────────
@router.put("/{candidate_id}/manual-resume")
async def save_manual_resume(
    candidate_id: int,
    manual_text: str = Form(...),
    db: Session = Depends(get_db),
):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.manual_resume_text = manual_text
    c.resume_version_type = "AI_SIMULATED"
    db.commit()
    return {"success": True}


# ── Role compatibility pre-check (called before JD alignment) ────────────────
@router.get("/{candidate_id}/role-compatibility")
def role_compatibility_check(candidate_id: int, db: Session = Depends(get_db)):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not c.client_requirement:
        raise HTTPException(status_code=400, detail="No JD selected")

    candidate_fn = detect_function(c.original_resume_text or "")
    jd_fn        = detect_function(c.client_requirement.jd_text or "")
    compat       = check_role_compatibility(candidate_fn, jd_fn)

    # Cache on the candidate record
    c.candidate_function = candidate_fn
    c.jd_function        = jd_fn
    c.role_compatibility = compat["verdict"]
    db.commit()

    return {
        "success": True,
        "verdict": compat["verdict"],
        "message": compat["message"],
        "color": compat.get("color", ""),
        "candidate_function": candidate_fn,
        "jd_function": jd_fn,
    }


# ── JD-Aligned approved candidates list ──────────────────────────────────────
@router.get("/list/jd-aligned")
def list_jd_aligned(db: Session = Depends(get_db)):
    candidates = (
        db.query(Candidate)
        .filter(Candidate.resume_version_type.in_(["AI_SIMULATED", "JD_ALIGNED"]))
        .order_by(Candidate.created_at.desc())
        .all()
    )
    return {"success": True, "data": [to_dict(c) for c in candidates]}


# ── Ollama status ─────────────────────────────────────────────────────────────
@router.get("/system/ollama-status")
def ollama_status():
    return get_provider_status()


# ── Helper ────────────────────────────────────────────────────────────────────
def _safe_json(val):
    if not val:
        return []
    try:
        return json.loads(val)
    except Exception:
        return []


def to_dict(c: Candidate):
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "mobile": c.mobile,
        "location": c.location,
        "linkedin": c.linkedin,
        "company_name": c.company_name,
        "role": getattr(c, "role", "") or "",
        "recruiter_name": getattr(c, "recruiter_name", "") or "",
        "score": c.score,
        "match_level": c.match_level or "",
        "status": c.status,
        "resume_version_type": c.resume_version_type or "ORIGINAL",
        "original_resume_text": c.original_resume_text or "",
        "improved_resume_text": c.improved_resume_text or "",
        "manual_resume_text": c.manual_resume_text or "",
        "recommendations":     _safe_json(c.recommendations),
        "gaps":                _safe_json(c.gaps),
        "strengths":           _safe_json(c.strengths),
        "must_have_missing":   _safe_json(getattr(c, "must_have_missing",   "[]")),
        "nice_to_have_missing":_safe_json(getattr(c, "nice_to_have_missing","[]")),
        "project_suggestions": _safe_json(getattr(c, "project_suggestions", "[]")),
        "align_history":       _safe_json(getattr(c, "align_history",       "[]")),
        "detected_domain":     getattr(c, "detected_domain", "") or "",
        "injection_supported": bool(getattr(c, "injection_supported", 1)),
        "soft_gaps":           _safe_json(getattr(c, "soft_gaps", "[]")),
        "semantic_score":      getattr(c, "semantic_score", None),
        "candidate_function":  getattr(c, "candidate_function", "") or "",
        "jd_function":         getattr(c, "jd_function", "") or "",
        "role_compatibility":  getattr(c, "role_compatibility", "") or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "client_requirement": {
            "id": c.client_requirement.id,
            "client_name": c.client_requirement.client_name,
            "job_title": c.client_requirement.job_title,
            "jd_text": c.client_requirement.jd_text,
        } if c.client_requirement else None,
    }