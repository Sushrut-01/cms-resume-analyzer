# =============================================================================
# client_requirements.py — Job Description (JD) Management
#
# A "client requirement" is a Job Description — the role Kforce is hiring for.
# Each uploaded resume is matched against one JD during AI analysis.
#
# Access levels:
#   GET  /client-requirements/        → get_current_user  (any recruiter can view JDs)
#   POST /client-requirements/        → require_admin     (only admin can create JDs)
#   PUT  /client-requirements/{id}    → require_admin     (only admin can edit JDs)
#   DELETE /client-requirements/{id}  → require_admin     (only admin can deactivate JDs)
#
# Note: DELETE does NOT permanently remove a JD — it sets active=False (soft delete).
# This preserves historical analysis data for candidates already matched to that JD.
# =============================================================================

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from deps import get_current_user, require_admin
from models.client_requirement import ClientRequirement
from schemas.client_requirement import ClientRequirementOut, ClientRequirementCreate


router = APIRouter(
    prefix="/client-requirements",
    tags=["Client Requirements"],
)


# -----------------------------------------------------------------------------
# GET /client-requirements/ — List All Active Job Descriptions
# Returns only active JDs (active=True), ordered by client name then job title.
# Used to populate the JD dropdown when uploading a resume.
# Any logged-in recruiter can view this list.
# -----------------------------------------------------------------------------
@router.get("/", response_model=List[ClientRequirementOut])
def list_jds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), _=Depends(get_current_user)):
    return (
        db.query(ClientRequirement)
        .filter(ClientRequirement.active == True)
        .order_by(ClientRequirement.client_name, ClientRequirement.job_title)
        .offset(skip)
        .limit(limit)
        .all()
    )


# -----------------------------------------------------------------------------
# POST /client-requirements/ — Create a New Job Description
#
# Required fields: client_name, job_title, jd_text (the full job description text)
# New JDs are always created with active=True.
# Only admins can create JDs — recruiters cannot add new roles.
# -----------------------------------------------------------------------------
@router.post("/", response_model=ClientRequirementOut)
def create_jd(payload: ClientRequirementCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    jd = ClientRequirement(
        client_name=payload.client_name,
        job_title=payload.job_title,
        jd_text=payload.jd_text,
        active=True,
    )
    db.add(jd)
    db.commit()
    db.refresh(jd)
    return jd


# -----------------------------------------------------------------------------
# PUT /client-requirements/{jd_id} — Update a Job Description
#
# Allows editing the client name, job title, and JD text.
# Only admins can edit JDs.
# Existing candidates linked to this JD will use the updated JD text
# on their next analysis run.
# -----------------------------------------------------------------------------
@router.put("/{jd_id}", response_model=ClientRequirementOut)
def update_jd(jd_id: int, payload: ClientRequirementCreate, db: Session = Depends(get_db), _=Depends(require_admin)):
    jd = db.query(ClientRequirement).filter(ClientRequirement.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    jd.client_name = payload.client_name
    jd.job_title   = payload.job_title
    jd.jd_text     = payload.jd_text
    db.commit()
    db.refresh(jd)
    return jd


# -----------------------------------------------------------------------------
# DELETE /client-requirements/{jd_id} — Deactivate a Job Description
#
# This is a SOFT DELETE — sets active=False, does not remove from database.
# Reason: candidates already uploaded against this JD retain their analysis history.
# Deactivated JDs no longer appear in the upload dropdown.
# Only admins can deactivate JDs.
# -----------------------------------------------------------------------------
@router.delete("/{jd_id}")
def delete_jd(jd_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    jd = db.query(ClientRequirement).filter(ClientRequirement.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    # Soft delete — preserve historical data, just hide from active list
    jd.active = False
    db.commit()
    return {"success": True, "message": "JD deactivated"}
