from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id = Column(Integer, primary_key=True)

    candidate_id = Column(
        Integer,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version_type = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)

    linked_jd_id = Column(
        Integer,
        ForeignKey("client_requirements.id"),
        nullable=True,
    )

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    candidate = relationship("Candidate", back_populates="resume_versions")
    linked_jd = relationship("ClientRequirement")

    __table_args__ = (
        Index("ix_resume_versions_candidate_type", "candidate_id", "version_type"),
    )