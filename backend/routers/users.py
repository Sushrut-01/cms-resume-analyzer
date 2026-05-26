from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["Users"])


def _valid_role(role: str, db: Session) -> bool:
    from models.role import Role
    return db.query(Role).filter(Role.name == role).first() is not None


def _hash(plain: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(plain)


def _user_dict(u) -> dict:
    return {
        "id":         u.id,
        "name":       u.name,
        "email":      u.email,
        "role":       u.role,
        "is_active":  u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


@router.get("/")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    from models.user import User
    users = db.query(User).order_by(User.created_at).all()
    return {"data": [_user_dict(u) for u in users]}


@router.post("/")
def create_user(payload: dict, db: Session = Depends(get_db), _=Depends(require_admin)):
    from models.user import User
    name     = (payload.get("name") or "").strip()
    email    = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    role     = payload.get("role") or "recruiter"

    if not name or not email or not password:
        raise HTTPException(status_code=400, detail="name, email, and password are required")
    if not _valid_role(role, db):
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already in use")

    user = User(name=name, email=email, password_hash=_hash(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"success": True, **_user_dict(user)}


@router.put("/{user_id}")
def update_user(user_id: int, payload: dict, db: Session = Depends(get_db), current=Depends(get_current_user)):
    from models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if current.role not in ("admin", "super_admin") and current.id != user_id:
        raise HTTPException(status_code=403, detail="Admin access required")

    if current.role in ("admin", "super_admin"):
        if "name"      in payload: user.name      = (payload["name"] or "").strip()
        if "role"      in payload:
            if not _valid_role(payload["role"], db):
                raise HTTPException(status_code=400, detail="Invalid role")
            user.role = payload["role"]
        if "is_active" in payload: user.is_active = bool(payload["is_active"])

    if payload.get("password"):
        user.password_hash = _hash(payload["password"])

    db.commit()
    return {"success": True}


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current=Depends(require_admin)):
    from models.user import User
    if current.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"success": True}
