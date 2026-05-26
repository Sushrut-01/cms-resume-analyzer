from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from database import Base


class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String(100), nullable=False)
    email         = Column(String(200), nullable=False, unique=True, index=True)
    password_hash = Column(String(200), nullable=False)
    role          = Column(String(20),  nullable=False, default="recruiter")  # admin | recruiter
    is_active     = Column(Boolean,     nullable=False, default=True)
    created_at    = Column(DateTime,    nullable=False, default=datetime.utcnow)
