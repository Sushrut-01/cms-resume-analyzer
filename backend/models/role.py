from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime
from database import Base


class Role(Base):
    __tablename__ = "roles"
    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(50),  nullable=False, unique=True, index=True)  # slug
    label       = Column(String(100), nullable=False)
    permissions = Column(Text,        nullable=False, default="[]")  # JSON array of permission strings
    is_system   = Column(Boolean,     nullable=False, default=False)
    created_at  = Column(DateTime,    nullable=False, default=datetime.utcnow)
