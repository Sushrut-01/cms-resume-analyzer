# =============================================================================
# recruiters.py — Recruiter Name Management
#
# Manages the list of recruiter names used when uploading resumes.
# A recruiter name is selected from a dropdown at upload time so the
# candidate record shows which recruiter submitted it.
#
# Note: This is a LEGACY simple name list — separate from the full User system.
# The Users page (users.py) is the main user management system with roles and login.
# This router manages a simpler "recruiter name" dropdown list only.
#
# Access: No authentication required on these endpoints (open list).
# The recruiter names are not sensitive — they are just display labels.
#
# Endpoints:
#   GET  /recruiters/         → list all active recruiter names
#   POST /recruiters/         → add a new recruiter name
#   DELETE /recruiters/{id}   → deactivate a recruiter name (soft delete)
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models.recruiter import Recruiter

router = APIRouter(prefix="/recruiters", tags=["Recruiters"])


# -----------------------------------------------------------------------------
# GET /recruiters/ — List All Active Recruiter Names
# Returns names ordered alphabetically.
# Only active=True entries are returned (deactivated names are hidden).
# Used to populate the recruiter dropdown on the upload form.
# -----------------------------------------------------------------------------
@router.get("/")
def list_recruiters(db: Session = Depends(get_db)):
    recruiters = (
        db.query(Recruiter)
        .filter(Recruiter.active == True)   # only show active names
        .order_by(Recruiter.name)           # alphabetical order
        .all()
    )
    # Return only id and name — nothing sensitive in this response
    return {"success": True, "data": [{"id": r.id, "name": r.name} for r in recruiters]}


# -----------------------------------------------------------------------------
# POST /recruiters/ — Add a New Recruiter Name
# Creates a new recruiter entry with the given name.
# Duplicate names (case-sensitive, active only) are rejected.
# -----------------------------------------------------------------------------
@router.post("/")
def create_recruiter(payload: dict, db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Recruiter name is required")

    # Check for duplicate active name — prevent creating "John" twice
    existing = db.query(Recruiter).filter(Recruiter.name == name, Recruiter.active == True).first()
    if existing:
        raise HTTPException(status_code=400, detail="Recruiter with this name already exists")

    r = Recruiter(name=name, active=True, created_at=datetime.utcnow())
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"success": True, "data": {"id": r.id, "name": r.name}}


# -----------------------------------------------------------------------------
# DELETE /recruiters/{recruiter_id} — Deactivate a Recruiter Name
# Soft delete — sets active=False instead of removing from database.
# Preserves historical records: candidates uploaded under this name
# retain their recruiter_name field value even after deactivation.
# -----------------------------------------------------------------------------
@router.delete("/{recruiter_id}")
def delete_recruiter(recruiter_id: int, db: Session = Depends(get_db)):
    r = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    # Soft delete — hide from dropdown but keep in DB for historical records
    r.active = False
    db.commit()
    return {"success": True}
