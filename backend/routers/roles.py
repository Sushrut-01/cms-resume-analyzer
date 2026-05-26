# =============================================================================
# roles.py — Role & Permission Management
#
# Roles define what actions each type of user can perform in K Recruit.
# Built-in system roles: super_admin, admin, recruiter
# Custom roles can be created by super_admin only.
#
# Permissions are stored as a JSON array in each role record.
# Example: ["upload_resume", "analyze", "view_candidates", "approve"]
#
# Access levels:
#   GET  /roles/        → get_current_user   (any logged-in user can see roles)
#   POST /roles/        → require_super_admin (only super_admin can create roles)
#   PUT  /roles/{id}    → require_super_admin (only super_admin can edit roles)
#   DELETE /roles/{id}  → require_super_admin (only super_admin can delete roles)
#
# System roles (super_admin, admin, recruiter) cannot be deleted.
# The super_admin role itself cannot be modified.
# =============================================================================

import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from deps import get_current_user, require_super_admin

router = APIRouter(prefix="/roles", tags=["Roles"])


# -----------------------------------------------------------------------------
# Helper: Convert a Role database record into a plain dictionary for the API response
# Permissions are stored as a JSON string in the DB — parsed back to a list here.
# -----------------------------------------------------------------------------
def _role_dict(r) -> dict:
    return {
        "id":          r.id,
        "name":        r.name,           # internal key e.g. "recruiter"
        "label":       r.label,          # display name e.g. "Recruiter"
        "permissions": json.loads(r.permissions),  # list of allowed actions
        "is_system":   r.is_system,      # True = built-in, cannot be deleted
        "created_at":  r.created_at.isoformat() if r.created_at else None,
    }


# -----------------------------------------------------------------------------
# GET /roles/ — List All Roles
# Returns all roles, system roles first, then custom roles by creation date.
# Any logged-in user can view roles (needed to populate dropdowns in the UI).
# -----------------------------------------------------------------------------
@router.get("/")
def list_roles(db: Session = Depends(get_db), _=Depends(get_current_user)):
    from models.role import Role
    roles = db.query(Role).order_by(Role.is_system.desc(), Role.created_at).all()
    return {"data": [_role_dict(r) for r in roles]}


# -----------------------------------------------------------------------------
# POST /roles/ — Create a Custom Role
#
# Only super_admin can create new roles.
# Role name is auto-formatted: lowercased and spaces replaced with underscores.
# Example: "Senior Recruiter" → stored as "senior_recruiter"
# Duplicate role names are rejected.
# Newly created roles are marked is_system=False (can be deleted later).
# -----------------------------------------------------------------------------
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

    # Permissions stored as JSON string in the database
    role = Role(name=name, label=label, permissions=json.dumps(perms), is_system=False)
    db.add(role); db.commit(); db.refresh(role)
    return {"success": True, **_role_dict(role)}


# -----------------------------------------------------------------------------
# PUT /roles/{role_id} — Update a Role's Label or Permissions
#
# Only super_admin can edit roles.
# The "super_admin" role itself is fully protected — cannot be changed.
# System roles (admin, recruiter) can have their permissions updated
# but their label cannot be changed (label is display-only for system roles).
# Custom roles can have both label and permissions updated.
# -----------------------------------------------------------------------------
@router.put("/{role_id}")
def update_role(role_id: int, payload: dict, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    from models.role import Role
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # The super_admin role is fully locked — no modifications allowed
    if role.name == "super_admin":
        raise HTTPException(status_code=400, detail="Super admin role cannot be modified")

    # Only custom (non-system) roles can have their label changed
    if not role.is_system:
        if "label" in payload: role.label = (payload["label"] or "").strip()

    # Permissions can be updated on any non-super_admin role
    if "permissions" in payload: role.permissions = json.dumps(payload["permissions"])
    db.commit()
    return {"success": True}


# -----------------------------------------------------------------------------
# DELETE /roles/{role_id} — Delete a Custom Role
#
# Only super_admin can delete roles.
# System roles (super_admin, admin, recruiter) cannot be deleted.
# Before deleting a role, ensure no users are assigned to it (handled in UI).
# -----------------------------------------------------------------------------
@router.delete("/{role_id}")
def delete_role(role_id: int, db: Session = Depends(get_db), _=Depends(require_super_admin)):
    from models.role import Role
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    # System roles are protected — they are required for the app to function
    if role.is_system:
        raise HTTPException(status_code=400, detail="System roles cannot be deleted")
    db.delete(role); db.commit()
    return {"success": True}
