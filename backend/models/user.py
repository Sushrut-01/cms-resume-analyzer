# =============================================================================
# models/user.py — User Database Table Definition
#
# Defines the "users" table in the database.
# Each row represents one K Recruit user account (recruiter, admin, super_admin).
#
# Key design decisions:
#   - password_hash stores the bcrypt hash, NEVER the plain text password
#   - password_hash can be NULL for Entra ID (Microsoft) users — they log in
#     via the "Sign in with Microsoft" button and have no K Recruit password
#   - email is the login identifier (unique, indexed for fast lookup)
#   - is_active=False disables a user's login without deleting their account
#   - role is a string matching a name in the roles table
# =============================================================================

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"

    # Primary key — auto-incremented integer ID
    id            = Column(Integer, primary_key=True, index=True)

    # Display name shown in the UI (e.g. "Sushrut Nistane")
    name          = Column(String(100), nullable=False)

    # Login email — must be unique across all users
    # For Kforce staff: firstname.lastname@kforce.co.in
    # Indexed for fast lookup during login
    email         = Column(String(200), nullable=False, unique=True, index=True)

    # bcrypt hash of the user's password
    # NULL is allowed — Entra ID (Microsoft) users have no password in K Recruit
    # They are authenticated by Microsoft and matched by email only
    password_hash = Column(String(200), nullable=True)

    # Role name — must match a name in the roles table
    # Default is "recruiter" (lowest privilege level)
    # Other values: "admin", "super_admin"
    role          = Column(String(20),  nullable=False, default="recruiter")

    # When False: user cannot log in (account disabled)
    # Deactivated users' existing JWT tokens are rejected on next request
    is_active     = Column(Boolean,     nullable=False, default=True)

    # Timestamp of account creation — set automatically, never updated
    created_at    = Column(DateTime,    nullable=False, default=datetime.utcnow)
