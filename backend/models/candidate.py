# =============================================================================
# models/candidate.py — Candidate Database Table Definition
#
# Defines the "candidates" table — the central table of K Recruit.
# Each row represents one uploaded resume and all AI analysis results for it.
#
# Data lifecycle:
#   1. Upload: basic info extracted from PDF, status="Uploaded"
#   2. Analyze: AI fills in score, gaps, strengths, etc. status="Bot Analyzed"
#   3. JD Align: AI rewrites resume. status="Bot Analyzed", version="JD_ALIGNED"
#   4. Approve: recruiter marks ready. status="Approved"
#
# Storage note: Lists (gaps, strengths, etc.) are stored as JSON strings
# because SQLite does not have a native array type. They are parsed back
# to Python lists in the to_dict() helper in candidates.py.
#
# PII note: original_resume_text contains the raw extracted PDF text including
# name, email, phone. PII is stripped BEFORE sending to AI — see llm_service.py.
# =============================================================================

from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    # Primary key — auto-incremented integer ID
    id = Column(Integer, primary_key=True, index=True)

    # ── Contact info (extracted from PDF) ─────────────────────────────────────
    name     = Column(String(100))   # Candidate's full name
    email    = Column(String(100))   # Personal email from resume
    mobile   = Column(String(20))    # Phone number from resume
    role     = Column(String(100))   # Job title from the JD (not from resume)
    location = Column(String(100))   # City extracted from resume
    linkedin = Column(String(255))   # LinkedIn profile URL

    # Company and recruiter context
    company_name   = Column(String(100))          # Client company name (from JD)
    recruiter_name = Column(String(100), default="")  # Which recruiter uploaded this

    # ── Job Description Link ──────────────────────────────────────────────────
    # Foreign key to client_requirements table
    # Every candidate must be linked to exactly one JD at upload time
    client_requirement_id = Column(
        Integer,
        ForeignKey("client_requirements.id"),
        nullable=False,
    )
    client_requirement = relationship("ClientRequirement")  # ORM join

    # ── Resume Content ────────────────────────────────────────────────────────
    original_resume_text = Column(Text)   # Raw text extracted from uploaded PDF
    improved_resume_text = Column(Text)   # AI-rewritten version after analysis
    manual_resume_text   = Column(Text)   # Recruiter-edited or AI-simulated version

    # Tracks which version is the "current" one for download
    # Values: "ORIGINAL" | "ANALYZED" | "JD_ALIGNED" | "AI_SIMULATED"
    resume_version_type = Column(String(50), default="ORIGINAL")

    # ── AI Analysis Results ───────────────────────────────────────────────────
    # All list fields stored as JSON strings (SQLite has no array type)
    recommendations      = Column(Text)                   # JSON — suggested improvements
    gaps                 = Column(Text)                   # JSON — missing skills vs JD
    strengths            = Column(Text)                   # JSON — matching skills
    score                = Column(Float, default=0.0)     # ATS keyword match score 0-100
    match_level          = Column(String(50), default="") # "Excellent" | "Good" | "Fair" | "Poor"
    must_have_missing    = Column(Text, default="[]")     # JSON — critical missing requirements
    nice_to_have_missing = Column(Text, default="[]")     # JSON — optional missing skills
    project_suggestions  = Column(Text, default="[]")     # JSON — which projects to enhance
    align_history        = Column(Text, default="[]")     # JSON — history of JD alignment runs
    detected_domain      = Column(String(100), default="")# e.g. "Testing & QA", "Finance"
    injection_supported  = Column(Integer, default=1)     # 1=full rewrite, 0=analysis only
    soft_gaps            = Column(Text, default="[]")     # JSON — skills present but not highlighted
    semantic_score       = Column(Float, nullable=True)   # Embedding similarity score 0-100
    structured_resume_json = Column(Text, nullable=True)  # JSON — structured data from Phase B AI

    # Role function matching (used for compatibility check before JD alignment)
    candidate_function = Column(String(100), default="")  # e.g. "QA / Testing"
    jd_function        = Column(String(100), default="")  # e.g. "Finance / Accounting"
    role_compatibility = Column(String(20),  default="")  # "compatible" | "adjacent" | "incompatible"

    # ── Status & File ─────────────────────────────────────────────────────────
    # Status tracks where the candidate is in the workflow
    # Values: "Uploaded" | "Bot Analyzed" | "Approved"
    status   = Column(String(50), default="Pending Review")
    pdf_path = Column(String(255))  # Path to uploaded PDF on disk

    # ── Data Isolation ────────────────────────────────────────────────────────
    # Which user uploaded this candidate — used to filter the candidates list
    # so each recruiter only sees their own uploads.
    # Admins and super_admins bypass this filter and see all candidates.
    # nullable=True: pre-existing records (before this column was added) are NULL,
    # which admins can still see — no historical data is lost.
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Resume Version History ────────────────────────────────────────────────
    # One candidate can have multiple resume versions (ORIGINAL, ANALYZED, JD_ALIGNED)
    # cascade="all, delete-orphan" means versions are deleted when candidate is deleted
    resume_versions = relationship(
        "ResumeVersion",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
