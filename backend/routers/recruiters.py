from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from database import get_db
from models.recruiter import Recruiter

router = APIRouter(prefix="/recruiters", tags=["Recruiters"])


@router.get("/")
def list_recruiters(db: Session = Depends(get_db)):
    recruiters = (
        db.query(Recruiter)
        .filter(Recruiter.active == True)
        .order_by(Recruiter.name)
        .all()
    )
    return {"success": True, "data": [{"id": r.id, "name": r.name} for r in recruiters]}


@router.post("/")
def create_recruiter(payload: dict, db: Session = Depends(get_db)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Recruiter name is required")
    existing = db.query(Recruiter).filter(Recruiter.name == name, Recruiter.active == True).first()
    if existing:
        raise HTTPException(status_code=400, detail="Recruiter with this name already exists")
    r = Recruiter(name=name, active=True, created_at=datetime.utcnow())
    db.add(r)
    db.commit()
    db.refresh(r)
    return {"success": True, "data": {"id": r.id, "name": r.name}}


@router.delete("/{recruiter_id}")
def delete_recruiter(recruiter_id: int, db: Session = Depends(get_db)):
    r = db.query(Recruiter).filter(Recruiter.id == recruiter_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Recruiter not found")
    r.active = False
    db.commit()
    return {"success": True}
