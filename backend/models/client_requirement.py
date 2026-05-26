# =============================================================================
# models/client_requirement.py — Job Description (JD) Table Definition
#
# Defines the "client_requirements" table.
# Each row represents one Job Description that Kforce is hiring for.
# Candidates are matched against a JD during AI analysis.
#
# Naming note: "client requirement" = the role a client company needs filled.
# In the UI this is shown as "Job Description" or "JD".
#
# Soft delete: When a JD is "deleted" via the API, active is set to False.
# The record is NOT removed from the database so historical candidate analysis
# data remains intact and traceable.
# =============================================================================

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class ClientRequirement(Base):
    __tablename__ = "client_requirements"

    # Primary key — auto-incremented integer ID
    id = Column(Integer, primary_key=True, index=True)

    # Name of the client company hiring for this role
    # Example: "Infosys", "TCS", "Wipro"
    client_name = Column(String(100), nullable=False)

    # The job title being hired for
    # Example: "Senior QA Engineer", "Java Developer"
    job_title   = Column(String(100), nullable=False)

    # The full job description text — this is what the AI compares resumes against
    # Contains required skills, responsibilities, experience requirements, etc.
    jd_text     = Column(Text, nullable=False)

    # Soft delete flag — False means "deleted" but data is preserved
    # Only active=True JDs appear in the upload dropdown
    active      = Column(Boolean, default=True, nullable=False)

    # Timestamp of JD creation — set automatically, never updated
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    # All candidates matched to this JD
    candidates = relationship("Candidate", back_populates="client_requirement")

    # All resume version snapshots linked to this JD
    resume_versions = relationship("ResumeVersion", back_populates="linked_jd")
