# =============================================================================
# users.py — User Account Management
#
# Handles creating, editing, and deleting K Recruit user accounts.
# Only admins and super_admins can manage users.
# Regular recruiters cannot access any of these endpoints.
#
# Access levels:
#   GET  /users/           → require_admin  (view all users)
#   POST /users/           → require_admin  (create a user)
#   PUT  /users/{id}       → get_current_user (admin edits any user; user edits own password only)
#   DELETE /users/{id}     → require_admin  (delete a user)
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user, require_admin

router = APIRouter(prefix="/users", tags=["Users"])


# -----------------------------------------------------------------------------
# Helper: Check if a role name exists in the database
# Prevents assigning a made-up role to a user.
# -----------------------------------------------------------------------------
def _valid_role(role: str, db: Session) -> bool:
    from models.role import Role
    return db.query(Role).filter(Role.name == role).first() is not None


# -----------------------------------------------------------------------------
# Helper: Hash a plain-text password using bcrypt
# Passwords are NEVER stored as plain text in the database.
# -----------------------------------------------------------------------------
def _hash(plain: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(plain)


# -----------------------------------------------------------------------------
# Helper: Convert a User database record into a plain dictionary for the API response
# Excludes the password hash — it is never sent to the browser.
# -----------------------------------------------------------------------------
def _user_dict(u) -> dict:
    return {
        "id":         u.id,
        "name":       u.name,
        "email":      u.email,
        "role":       u.role,
        "is_active":  u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


# -----------------------------------------------------------------------------
# GET /users/ — List All Users
# Returns all user accounts ordered by creation date.
# Only admins and super_admins can see this list.
# Password hashes are never included in the response.
# -----------------------------------------------------------------------------
@router.get("/")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    from models.user import User
    users = db.query(User).order_by(User.created_at).all()
    return {"data": [_user_dict(u) for u in users]}


# -----------------------------------------------------------------------------
# POST /users/ — Create a New User Account
#
# Required fields: name, email
# Optional fields: password (omit for Microsoft/Entra ID login users), role (default: recruiter)
#
# Password is optional — Entra ID (Microsoft) users do not need one.
# They log in via the "Sign in with Microsoft" button using their Kforce account.
# If a password is provided, it is bcrypt-hashed before storing.
# Email must be unique — duplicate emails are rejected.
# -----------------------------------------------------------------------------
@router.post("/")
def create_user(payload: dict, db: Session = Depends(get_db), _=Depends(require_admin)):
    from models.user import User
    name     = (payload.get("name") or "").strip()
    email    = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    role     = payload.get("role") or "recruiter"

    if not name or not email:
        raise HTTPException(status_code=400, detail="name and email are required")
    if not _valid_role(role, db):
        raise HTTPException(status_code=400, detail="Invalid role")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already in use")

    # Password optional — Entra ID users log in via Microsoft, no password needed
    pwd_hash = _hash(password) if password else None
    user = User(name=name, email=email, password_hash=pwd_hash, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"success": True, **_user_dict(user)}


# -----------------------------------------------------------------------------
# PUT /users/{user_id} — Update a User Account
#
# Two permission levels:
#   Admin / Super Admin: can update name, email, role, is_active, and password
#   Regular user (self only): can only update their own password
#
# Email uniqueness is enforced — cannot assign an email already used by another account.
# Role must exist in the roles table.
# is_active=False disables login for that user immediately (next request will fail JWT check).
# -----------------------------------------------------------------------------
@router.put("/{user_id}")
def update_user(user_id: int, payload: dict, db: Session = Depends(get_db), current=Depends(get_current_user)):
    from models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Non-admin users can only edit their own account
    if current.role not in ("admin", "super_admin") and current.id != user_id:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Admin-only fields: name, email, role, active status
    if current.role in ("admin", "super_admin"):
        if "name"  in payload: user.name  = (payload["name"] or "").strip()
        if "email" in payload:
            new_email = (payload["email"] or "").strip().lower()
            if not new_email:
                raise HTTPException(status_code=400, detail="Email cannot be empty")
            # Check no other user already uses this email
            conflict = db.query(User).filter(User.email == new_email, User.id != user_id).first()
            if conflict:
                raise HTTPException(status_code=400, detail="Email already in use by another account")
            user.email = new_email
        if "role" in payload:
            if not _valid_role(payload["role"], db):
                raise HTTPException(status_code=400, detail="Invalid role")
            user.role = payload["role"]
        if "is_active" in payload: user.is_active = bool(payload["is_active"])

    # Password change is allowed for both admins and the user themselves
    if payload.get("password"):
        user.password_hash = _hash(payload["password"])

    db.commit()
    return {"success": True}


# -----------------------------------------------------------------------------
# DELETE /users/{user_id} — Delete a User Account
#
# Permanently removes the user from the database.
# An admin cannot delete their own account (safety guard).
# -----------------------------------------------------------------------------
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
