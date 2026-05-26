import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user, require_super_admin

router = APIRouter(prefix="/roles", tags=["Roles"])


def _role_dict(r) -> dict:
    return {
        "id":          r.id,
        "name":        r.name,
        "label":       r.label,
        "permissions": json.loads(r.permissions),
        "is_system":   r.is_system,
        "created_at":  r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/")
def list_roles(db: Session = Depends(get_db), _=Depends(get_current_user)):
    from models.role import Role
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.created_at).all()
    return {"data": [_role_dict(r) for r in roles]}


@router.post("/")
def create_role(payload: dict, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    from models.role import Role
    name  = (payload.get("name") or "").strip().lower().replace(" ", "_")
    label = (payload.get("label") or "").strip()
    perms = payload.get("permissions") or []
    if not name or not label:
        raise HTTPException(status_code=400, detail="name and label are required")
    if db.query(Role).filter(Role.name == name).first():
        raise HTTPException(status_code=400, detail="Role name already in use")

    role = Role(name=name, label=label, permissions=json.dumps(perms), is_system=False)
    db.add(role); db.commit(); db.refresh(role)
    return {"success": True, **_role_dict(role)}


@router.put("/{role_id}")
def update_role(role_id: int, payload: dict, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    from models.role import Role
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.name == "super_admin":
        raise HTTPException(status_code=400, detail="Super admin role cannot be modified")
    if not role.is_system:
        if "label" in payload: role.label = (payload["label"] or "").strip()
    if "permissions" in payload: role.permissions = json.dumps(payload["permissions"])
    db.commit()
    return {"success": True}


@router.delete("/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    from models.role import Role
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    db.delete(role); db.commit()
    return {"success": True}
