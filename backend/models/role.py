# =============================================================================
# models/role.py — Role Database Table Definition
#
# Defines the "roles" table in the database.
# Each row represents one user role with its associated permissions.
#
# Built-in system roles (seeded on first startup, cannot be deleted):
#   super_admin → full access to everything including role management
#   admin       → full access except role management
#   recruiter   → can upload, analyze, align, and download resumes only
#
# Custom roles can be created by super_admin via the Roles page.
# Permissions are stored as a JSON array of permission strings.
# Example: ["page:dashboard", "page:upload", "action:analyze_candidate"]
# =============================================================================

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime
from database import Base


class Role(Base):
    __tablename__ = "roles"

    # Primary key — auto-incremented integer ID
    id          = Column(Integer, primary_key=True, index=True)

    # Internal identifier slug — lowercase, underscores, unique
    # Examples: "super_admin", "admin", "recruiter", "senior_recruiter"
    # This is what gets stored in the User.role column
    name        = Column(String(50),  nullable=False, unique=True, index=True)

    # Human-readable display name shown in the UI
    # Examples: "Super Admin", "Admin", "Recruiter", "Senior Recruiter"
    label       = Column(String(100), nullable=False)

    # JSON array of permission strings that this role grants
    # Format: ["page:dashboard", "page:upload", "action:analyze_candidate"]
    # Pages control which sidebar items are visible
    # Actions control which buttons/features are enabled
    permissions = Column(Text,        nullable=False, default="[]")

    # True = built-in system role (cannot be deleted)
    # False = custom role created by super_admin (can be deleted)
    is_system   = Column(Boolean,     nullable=False, default=False)

    # Timestamp of role creation — set automatically, never updated
    created_at  = Column(DateTime,    nullable=False, default=datetime.utcnow)
