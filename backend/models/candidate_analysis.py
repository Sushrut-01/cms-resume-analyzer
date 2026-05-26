# =============================================================================
# models/candidate_analysis.py — Candidate Analysis Version History Table
#
# Defines the "candidate_analysis" table.
# Stores historical analysis snapshots — each time a candidate is analyzed,
# a new version record is created here.
#
# Note: This is a SECONDARY history table. The primary analysis data lives
# on the Candidate model itself (score, gaps, strengths, etc.).
# This table captures versioned snapshots for audit/comparison purposes.
#
# Relationship: Many analysis records can belong to one candidate.
# ondelete="CASCADE" means if the candidate is deleted, all their analysis
# history records are automatically deleted too — no orphaned records.
# =============================================================================

from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime, Float
from datetime import datetime
from backend.database import Base


class CandidateAnalysis(Base):
    __tablename__ = "candidate_analysis"

    # Primary key — auto-incremented integer ID
    id = Column(Integer, primary_key=True)

    # Which candidate this analysis snapshot belongs to
    # ondelete="CASCADE" — automatically deleted when candidate is deleted
    candidate_id = Column(
        Integer,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False
    )

    # Version number — increments each time the candidate is re-analyzed
    # Allows comparing version 1 vs version 2 results for the same candidate
    version = Column(Integer, nullable=False)

    # JSON string — the parsed resume sections at the time of this analysis run
    # Example: {"summary": "...", "skills": [...], "experience": [...]}
    sections = Column(Text)

    # JSON string — list of improvement recommendations from this analysis run
    recommendations = Column(Text)

    # AI match score (0.0 to 100.0) at the time of this analysis run
    score = Column(Float)

    # Timestamp when this analysis version was created — set automatically
    created_at = Column(DateTime, default=datetime.utcnow)
