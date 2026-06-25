# =============================================================================
# candidates.py — Resume Upload, AI Analysis & Download
#
# This is the core workflow file. It handles everything from uploading a PDF
# to running AI analysis to downloading the final Word document.
#
# Endpoint summary:
#   GET  /candidates/                          → list all candidates
#   GET  /candidates/{id}                      → get one candidate's full data
#   POST /candidates/upload                    → upload a PDF resume
#   POST /candidates/{id}/analyze              → run AI analysis against a JD
#   POST /candidates/{id}/generate-jd-aligned-resume → generate JD-aligned resume draft
#   GET  /candidates/{id}/download-resume      → download as Word (.docx) file
#   DELETE /candidates/{id}                    → delete candidate + uploaded file
#   PUT  /candidates/{id}/approve              → mark candidate as Approved
#   PUT  /candidates/{id}/update               → save edited resume text
#   PUT  /candidates/{id}/manual-resume        → save manual/AI-simulated resume
#   GET  /candidates/{id}/role-compatibility   → check if candidate role matches JD role
#   GET  /candidates/list/jd-aligned           → list all JD-aligned/AI-simulated candidates
#   GET  /candidates/system/ollama-status      → check AI provider status
#
# Access: all endpoints require get_current_user (any logged-in user)
# PII STRIPPING: before any AI call, contact details (email, phone, LinkedIn,
# GitHub, name, address) are stripped from resume text so they never reach the AI.
# =============================================================================

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    Depends,
    HTTPException,
    BackgroundTasks,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session
from datetime import datetime
import os
import shutil
import json
import threading

from database import get_db, SessionLocal
from deps import get_current_user
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

# Upload folder path — set via UPLOAD_FOLDER in .env, defaults to ./uploads
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "./uploads")

# Global lock — ensures only one Ollama call runs at a time
# Multiple recruiters can click Analyse simultaneously; requests queue here
_ollama_lock = threading.Lock()


def _run_analysis_background(candidate_id: int, user_id: int = None, user_email: str = None):
    """Background thread: runs Ollama analysis and saves results. One at a time via lock."""
    db = SessionLocal()
    try:
        c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if not c or c.status != "Processing":
            return

        with _ollama_lock:  # Queue — only one Ollama call at a time
            # Re-fetch inside lock in case status changed while waiting
            db.refresh(c)
            if c.status != "Processing":
                return

            result = analyze_resume(
                resume_text=c.original_resume_text,
                jd_text=c.client_requirement.jd_text,
                user_id=user_id,
                user_email=user_email,
                candidate_id=candidate_id,
            )

        if not result.get("success"):
            c.status = "Uploaded"  # Reset so recruiter can retry
            db.commit()
            return

        data = result["data"]
        c.score                = data.get("score", 0)
        c.match_level          = data.get("match_level", "")
        c.improved_resume_text = data.get("final_resume", "")
        c.recommendations      = json.dumps(data.get("recommendations", []))
        c.gaps                 = json.dumps(data.get("gaps", []))
        c.strengths            = json.dumps(data.get("strengths", []))
        c.must_have_missing    = json.dumps(data.get("must_have_missing", []))
        c.nice_to_have_missing = json.dumps(data.get("nice_to_have_missing", []))
        c.project_suggestions  = json.dumps(data.get("project_suggestions", []))
        c.detected_domain      = data.get("detected_domain", "")
        c.injection_supported  = 1 if data.get("injection_supported", True) else 0
        c.soft_gaps            = json.dumps(data.get("soft_gaps", []))
        c.semantic_score       = data.get("semantic_score")
        c.candidate_function   = data.get("candidate_function", "")
        c.jd_function          = data.get("jd_function", "")
        compat = data.get("role_compatibility", {})
        c.role_compatibility   = compat.get("verdict", "") if isinstance(compat, dict) else ""
        c.status               = "Bot Analyzed"
        c.resume_version_type  = "ANALYZED"
        c.updated_at           = datetime.utcnow()

        db.add(ResumeVersion(
            candidate_id=c.id,
            version_type="ANALYZED",
            content=c.improved_resume_text,
            linked_jd_id=c.client_requirement.id,
        ))
        db.commit()
    except Exception:
        try:
            c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            if c and c.status == "Processing":
                c.status = "Uploaded"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _get_candidate(db, candidate_id: int, current_user):
    """Fetch a candidate by ID with data isolation.
    Recruiters can only access candidates they uploaded.
    Admins and super_admins can access any candidate.
    Returns None if not found or access denied."""
    q = db.query(Candidate).filter(Candidate.id == candidate_id)
    if current_user.role == "recruiter":
        q = q.filter(Candidate.uploaded_by == current_user.id)
    return q.first()


# -----------------------------------------------------------------------------
# GET /candidates/ — List Candidates
# Recruiters: only see candidates they uploaded (uploaded_by = their user ID).
# Admins and super_admins: see all candidates regardless of who uploaded them.
# This is the core data isolation guard — prevents recruiter A seeing recruiter B's work.
# -----------------------------------------------------------------------------
@router.get("/")
def get_all_candidates(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    q = db.query(Candidate)
    if current_user.role == "recruiter":
        # Filter: only candidates this recruiter uploaded
        q = q.filter(Candidate.uploaded_by == current_user.id)
    candidates = q.order_by(Candidate.created_at.desc()).all()
    return {"success": True, "data": [to_dict(c) for c in candidates]}


# -----------------------------------------------------------------------------
# GET /candidates/{candidate_id} — Get One Candidate
# Returns full details for a single candidate including all resume versions.
# Returns 404 if the candidate does not exist OR if a recruiter tries to fetch
# a candidate they did not upload (prevents direct ID lookup bypass).
# -----------------------------------------------------------------------------
@router.get("/{candidate_id}")
def get_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return {"success": True, "data": to_dict(c)}


# -----------------------------------------------------------------------------
# POST /candidates/upload — Upload a PDF Resume
#
# Steps:
#   1. Validate the selected JD exists and is active
#   2. Save the PDF file to the uploads folder with a timestamp prefix
#   3. Extract text from the PDF (name, email, phone, skills, full text)
#   4. Create a new Candidate record in the database
#   5. Save an ORIGINAL resume version snapshot
#   6. Return extracted contact details for the UI to display
#
# The PDF is stored on disk. Extracted text is stored in the database.
# Status is set to "Uploaded" — analysis runs separately.
# -----------------------------------------------------------------------------
@router.post("/upload")
async def upload_resume(
    file: UploadFile = File(...),
    client_requirement_id: int = Form(...),
    recruiter_name: str = Form(""),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # Verify the selected JD exists and is still active
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

    # Save PDF with timestamp prefix to avoid filename collisions
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{file.filename}"
    pdf_path = os.path.join(UPLOAD_FOLDER, filename)

    with open(pdf_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Extract text and contact details from the PDF
    extracted = extract_text_from_pdf(pdf_path)
    if not extracted.get("success"):
        raise HTTPException(
            status_code=400,
            detail=f"PDF extraction failed: {extracted.get('error')}",
        )

    # Create the candidate record — status starts as "Uploaded"
    # uploaded_by is set to the current user's ID for data isolation
    candidate = Candidate(
        name=extracted.get("name"),
        email=extracted.get("email"),
        mobile=extracted.get("mobile"),
        location=extracted.get("location", ""),
        linkedin=extracted.get("linkedin", ""),
        company_name=client_req.client_name,
        role=client_req.job_title,
        recruiter_name=recruiter_name or "",
        uploaded_by=current_user.id,
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

    # Save a version snapshot so the original is never overwritten
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


# -----------------------------------------------------------------------------
# POST /candidates/{candidate_id}/analyze — Run AI Analysis
#
# This is the main AI processing step. It:
#   1. Loads the candidate's resume text and their linked JD text
#   2. Strips all PII (name, email, phone, LinkedIn, GitHub) before sending to AI
#   3. Sends resume + JD to the AI for gap analysis and scoring
#   4. Saves all results back to the candidate record:
#      - score (0-100%), match_level, strengths, gaps, recommendations
#      - must-have missing skills, nice-to-have missing skills
#      - semantic score (embedding similarity), detected domain
#      - improved resume text (AI-rewritten version)
#   5. Saves an ANALYZED version snapshot
#   6. Sets status to "Bot Analyzed"
#
# Requires: JD must be linked, active, and have enough text (>30 chars)
# Requires: AI provider must be running (checks Ollama/Azure/Groq status)
# -----------------------------------------------------------------------------
@router.post("/{candidate_id}/analyze")
def analyze_candidate(candidate_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    if not c.client_requirement:
        raise HTTPException(status_code=400, detail="Client requirement (JD) is not selected")

    if not c.client_requirement.active:
        raise HTTPException(status_code=400, detail="Client requirement is not active")

    if len(c.client_requirement.jd_text.strip()) < 30:
        raise HTTPException(status_code=400, detail="Client requirement text is too short")

    if not check_ollama_status():
        raise HTTPException(status_code=503, detail="Ollama is not running — start Ollama and try again")

    if c.status == "Processing":
        return {"success": True, "queued": True, "message": "Analysis already in progress"}

    # Set status to Processing immediately and return — analysis runs in background
    c.status = "Processing"
    c.updated_at = datetime.utcnow()
    db.commit()

    background_tasks.add_task(_run_analysis_background, candidate_id, current_user.id, current_user.email)

    return {"success": True, "queued": True, "message": "Analysis started — results will appear shortly"}


# -----------------------------------------------------------------------------
# POST /candidates/{candidate_id}/generate-jd-aligned-resume — Generate JD-Aligned Resume
#
# A deeper step than analysis — rewrites the resume to specifically match the JD.
# It may add missing skills, rephrase bullet points, and suggest new projects.
# Each run is logged in align_history so recruiters can compare runs.
# PII stripping happens inside generate_aligned_resume() before the AI call.
# -----------------------------------------------------------------------------
@router.post("/{candidate_id}/generate-jd-aligned-resume")
def generate_jd_aligned(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Analysis must be completed before JD alignment — alignment uses AI score,
    # gaps, and strengths from analysis to decide what to rewrite.
    if c.status not in ("Bot Analyzed", "Approved"):
        raise HTTPException(
            status_code=400,
            detail="Resume must be analyzed before JD alignment. Please run Analyze first.",
        )

    if not c.client_requirement:
        raise HTTPException(
            status_code=400,
            detail="Client requirement (JD) is not selected",
        )

    if not check_ollama_status():
        raise HTTPException(status_code=503, detail="Ollama LLM is not running")

    # Run JD alignment — PII stripped inside this function
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

    # Append this run to the candidate's alignment history (kept for comparison)
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

    # Save a version snapshot of the JD-aligned resume
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


# -----------------------------------------------------------------------------
# GET /candidates/{candidate_id}/download-resume — Download Resume as Word File
#
# Generates a .docx (Microsoft Word) file from the candidate's resume text.
# Two paths:
#   1. Structured JSON path: used when AI returned structured data (Groq/Gemini/Azure)
#      — produces a well-formatted Word document with sections
#   2. Text path: fallback for Ollama or older records — generates from plain text
#
# Priority of resume content used:
#   manual_resume_text (if recruiter edited) → improved_resume_text (AI output) → original
# -----------------------------------------------------------------------------
@router.get("/{candidate_id}/download-resume")
def download_resume(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")

    # Try structured JSON path first (better formatting)
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

    # Fall back to plain text path if structured data unavailable
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

    # Return as a file download response — browser prompts "Save As"
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


# -----------------------------------------------------------------------------
# DELETE /candidates/{candidate_id} — Delete a Candidate
# Removes the candidate record from the database.
# Also deletes the uploaded PDF file from disk if it exists.
# -----------------------------------------------------------------------------
@router.delete("/{candidate_id}")
def delete_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    # Remove the uploaded PDF file from disk
    if c.resume_path and os.path.exists(c.resume_path):
        try:
            os.remove(c.resume_path)
        except OSError:
            pass
    db.delete(c)
    db.commit()
    return {"success": True, "message": f"Candidate {candidate_id} deleted"}


# -----------------------------------------------------------------------------
# PUT /candidates/{candidate_id}/approve — Approve a Candidate
# Sets the candidate's status to "Approved".
# Used by recruiters to mark candidates who are ready to be submitted.
# -----------------------------------------------------------------------------
@router.put("/{candidate_id}/approve")
def approve_candidate(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.status = "Approved"
    db.commit()
    return {"success": True, "status": "Approved"}


# -----------------------------------------------------------------------------
# PUT /candidates/{candidate_id}/update — Save Edited Resume Text
# Allows saving changes made to the improved_resume_text in the editor.
# -----------------------------------------------------------------------------
@router.put("/{candidate_id}/update")
async def update_resume(
    candidate_id: int,
    improved_text: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.improved_resume_text = improved_text
    db.commit()
    return {"success": True}


# -----------------------------------------------------------------------------
# PUT /candidates/{candidate_id}/manual-resume — Save Manual/AI-Simulated Resume
# Saves the manually edited or AI-simulated resume draft.
# Sets version type to "AI_SIMULATED" to indicate it was manually crafted.
# -----------------------------------------------------------------------------
@router.put("/{candidate_id}/manual-resume")
async def save_manual_resume(
    candidate_id: int,
    manual_text: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    c.manual_resume_text = manual_text
    c.resume_version_type = "AI_SIMULATED"
    db.commit()
    return {"success": True}


# -----------------------------------------------------------------------------
# GET /candidates/{candidate_id}/role-compatibility — Pre-Check Role Match
#
# Checks whether the candidate's job function matches the JD's job function
# BEFORE running the full JD alignment (which is more expensive).
# Example: "Software Engineer" resume vs "Finance Analyst" JD → mismatch warning
#
# Results are cached on the candidate record to avoid re-running.
# Returns: verdict (Compatible/Partial/Incompatible), message, color for UI badge
# -----------------------------------------------------------------------------
@router.get("/{candidate_id}/role-compatibility")
def role_compatibility_check(candidate_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    c = _get_candidate(db, candidate_id, current_user)
    if not c:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if not c.client_requirement:
        raise HTTPException(status_code=400, detail="No JD selected")

    # Detect the function/domain of both the resume and the JD
    candidate_fn = detect_function(c.original_resume_text or "")
    jd_fn        = detect_function(c.client_requirement.jd_text or "")
    compat       = check_role_compatibility(candidate_fn, jd_fn)

    # Cache results on the candidate to avoid re-running unnecessarily
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


# -----------------------------------------------------------------------------
# GET /candidates/list/jd-aligned — List JD-Aligned Candidates
# Returns only candidates whose resume has been JD-aligned or AI-simulated.
# Used by the download/export view to show submission-ready candidates.
# Same data isolation as GET /candidates/ — recruiters see only their own.
# -----------------------------------------------------------------------------
@router.get("/list/jd-aligned")
def list_jd_aligned(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    q = (
        db.query(Candidate)
        .filter(Candidate.resume_version_type.in_(["AI_SIMULATED", "JD_ALIGNED"]))
    )
    if current_user.role == "recruiter":
        q = q.filter(Candidate.uploaded_by == current_user.id)
    candidates = q.order_by(Candidate.created_at.desc()).all()
    return {"success": True, "data": [to_dict(c) for c in candidates]}


# -----------------------------------------------------------------------------
# GET /candidates/system/ollama-status — Check AI Provider Status
# Returns whether the configured AI provider (Ollama/Azure/Groq) is reachable.
# Used by the UI to show the online/offline indicator.
# This endpoint does NOT require authentication (status check is harmless).
# -----------------------------------------------------------------------------
@router.get("/system/ollama-status")
def ollama_status():
    return get_provider_status()


# -----------------------------------------------------------------------------
# Helper: Safely parse a JSON string stored in the database
# Returns an empty list if the value is None or not valid JSON.
# Used because analysis results are stored as JSON strings in SQLite.
# -----------------------------------------------------------------------------
def _safe_json(val):
    if not val:
        return []
    try:
        return json.loads(val)
    except Exception:
        return []


# -----------------------------------------------------------------------------
# Helper: Convert a Candidate database record into a full dictionary
# This is what gets returned in API responses.
# All JSON string fields (gaps, strengths, etc.) are parsed back to lists.
# -----------------------------------------------------------------------------
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
        "uploaded_by":         getattr(c, "uploaded_by", None),
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "client_requirement": {
            "id": c.client_requirement.id,
            "client_name": c.client_requirement.client_name,
            "job_title": c.client_requirement.job_title,
            "jd_text": c.client_requirement.jd_text,
        } if c.client_requirement else None,
    }
