from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Float,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class Candidate(Base):
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(100))
    email = Column(String(100))
    mobile = Column(String(20))
    role = Column(String(100))
    location = Column(String(100))
    linkedin = Column(String(255))
    company_name = Column(String(100))
    recruiter_name = Column(String(100), default="")

    # ✅ FIX: Proper JD relationship
    client_requirement_id = Column(
        Integer,
        ForeignKey("client_requirements.id"),
        nullable=False,
    )
    client_requirement = relationship("ClientRequirement")

    # Resume content
    original_resume_text = Column(Text)
    improved_resume_text = Column(Text)
    manual_resume_text = Column(Text)

    resume_version_type = Column(String(50), default="ORIGINAL")

    # AI results
    recommendations = Column(Text)          # JSON
    gaps = Column(Text)                     # JSON
    strengths = Column(Text)               # JSON
    score = Column(Float, default=0.0)
    match_level = Column(String(50), default="")
    must_have_missing = Column(Text, default="[]")   # JSON — critical missing tech skills
    nice_to_have_missing = Column(Text, default="[]")# JSON — optional missing skills
    project_suggestions = Column(Text, default="[]") # JSON — which projects to enhance
    align_history = Column(Text, default="[]")       # JSON — history of all alignment runs
    detected_domain = Column(String(100), default="")# e.g. "Testing & QA", "Finance"
    injection_supported = Column(Integer, default=1) # 1=full, 0=analysis-only
    soft_gaps = Column(Text, default="[]")            # JSON — semantic soft gaps with suggestions
    semantic_score = Column(Float, nullable=True)     # overall semantic similarity 0-100
    structured_resume_json = Column(Text, nullable=True)  # JSON — structured resume from Phase B extraction
    candidate_function = Column(String(100), default="")  # detected job function e.g. "QA / Testing"
    jd_function        = Column(String(100), default="")  # JD's required function
    role_compatibility = Column(String(20),  default="")  # compatible | adjacent | incompatible

    # Status & files
    status = Column(String(50), default="Pending Review")
    pdf_path = Column(String(255))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # ✅ Resume history
    resume_versions = relationship(
        "ResumeVersion",
        back_populates="candidate",
        cascade="all, delete-orphan",
    )