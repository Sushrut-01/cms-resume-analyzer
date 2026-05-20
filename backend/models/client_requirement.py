from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
)
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class ClientRequirement(Base):
    __tablename__ = "client_requirements"

    id = Column(Integer, primary_key=True, index=True)

    client_name = Column(String(100), nullable=False)
    job_title = Column(String(100), nullable=False)
    jd_text = Column(Text, nullable=False)

    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    candidates = relationship(
        "Candidate",
        back_populates="client_requirement",
    )

    resume_versions = relationship(
        "ResumeVersion",
        back_populates="linked_jd",
    )