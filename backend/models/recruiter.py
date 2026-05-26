# =============================================================================
# models/recruiter.py — Recruiter Name Table Definition
#
# Defines the "recruiters" table.
# Stores the list of recruiter names shown in the upload form dropdown.
# Recruiters select their name when uploading a resume so the candidate
# record shows which recruiter submitted it.
#
# Note: This is a SIMPLE name list — separate from the full User system.
# The users table (models/user.py) handles login, roles, and authentication.
# This table only stores display names for the upload form dropdown.
#
# Soft delete: active=False hides a name from the dropdown without deleting
# historical candidate records that reference that recruiter name.
# =============================================================================

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from database import Base


class Recruiter(Base):
    __tablename__ = "recruiters"

    # Primary key — auto-incremented integer ID
    id = Column(Integer, primary_key=True, index=True)

    # Recruiter's display name shown in the upload dropdown
    # Example: "Sushrut Nistane", "Priya Sharma"
    name = Column(String(100), nullable=False)

    # Soft delete flag — False hides this name from the dropdown
    # Historical candidates uploaded under this name are unaffected
    active = Column(Boolean, default=True, nullable=False)

    # Timestamp when this recruiter name was added — set automatically
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
