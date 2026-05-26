# =============================================================================
# models/resume_version.py — Resume Version History Table Definition
#
# Defines the "resume_versions" table.
# Tracks every version of a candidate's resume throughout the workflow.
#
# Why version history matters:
#   - Recruiters can compare the original resume vs the AI-improved version
#   - Provides an audit trail of all AI modifications
#   - Prevents data loss if a recruiter wants to revert to an earlier version
#
# Version types saved automatically:
#   ORIGINAL   → saved when the PDF is first uploaded
#   ANALYZED   → saved after AI analysis runs (AI-rewritten resume)
#   JD_ALIGNED → saved after each JD alignment run
#
# Versions are linked to both the candidate AND the JD that was used,
# so you can see exactly which JD was used to produce each version.
# =============================================================================

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from database import Base


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    # Primary key — auto-incremented integer ID
    id = Column(Integer, primary_key=True)

    # Which candidate this version belongs to
    # ondelete="CASCADE": if the candidate is deleted, all their versions are deleted too
    candidate_id = Column(
        Integer,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Type of version — tracks which step in the workflow produced this version
    # Values: "ORIGINAL" | "ANALYZED" | "JD_ALIGNED" | "AI_SIMULATED"
    version_type = Column(String(30), nullable=False)

    # The full resume text at this point in the workflow
    content = Column(Text, nullable=False)

    # Which JD was used when this version was created
    # Nullable — ORIGINAL versions don't have an associated JD rewrite
    linked_jd_id = Column(
        Integer,
        ForeignKey("client_requirements.id"),
        nullable=True,
    )

    # Timestamp when this version was saved — set automatically
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ── Relationships ─────────────────────────────────────────────────────────
    candidate = relationship("Candidate", back_populates="resume_versions")
    linked_jd = relationship("ClientRequirement")

    # Composite index for fast queries: "all versions for candidate X of type Y"
    __table_args__ = (
        Index("ix_resume_versions_candidate_type", "candidate_id", "version_type"),
    )
