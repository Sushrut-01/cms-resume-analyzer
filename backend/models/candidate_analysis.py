from sqlalchemy import Column, Integer, ForeignKey, Text, DateTime, Float
from datetime import datetime
from backend.database import Base

class CandidateAnalysis(Base):
    __tablename__ = "candidate_analysis"

    id = Column(Integer, primary_key=True)
    candidate_id = Column(
        Integer,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False
    )

    version = Column(Integer, nullable=False)
    sections = Column(Text)
    recommendations = Column(Text)
    score = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)