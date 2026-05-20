from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models.client_requirement import ClientRequirement
from schemas.client_requirement import ClientRequirementOut, ClientRequirementCreate


router = APIRouter(
    prefix="/client-requirements",
    tags=["Client Requirements"],
)


@router.get("/", response_model=List[ClientRequirementOut])
def list_jds(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(ClientRequirement)
        .filter(ClientRequirement.active == True)
        .order_by(ClientRequirement.client_name, ClientRequirement.job_title)
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.post("/", response_model=ClientRequirementOut)
def create_jd(payload: ClientRequirementCreate, db: Session = Depends(get_db)):
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


@router.put("/{jd_id}", response_model=ClientRequirementOut)
def update_jd(jd_id: int, payload: ClientRequirementCreate, db: Session = Depends(get_db)):
    jd = db.query(ClientRequirement).filter(ClientRequirement.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    jd.client_name = payload.client_name
    jd.job_title   = payload.job_title
    jd.jd_text     = payload.jd_text
    db.commit()
    db.refresh(jd)
    return jd


@router.delete("/{jd_id}")
def delete_jd(jd_id: int, db: Session = Depends(get_db)):
    jd = db.query(ClientRequirement).filter(ClientRequirement.id == jd_id).first()
    if not jd:
        raise HTTPException(status_code=404, detail="JD not found")
    jd.active = False
    db.commit()
    return {"success": True, "message": "JD deactivated"}