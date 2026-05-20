from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import pathlib
import os

# ── Database URL ──────────────────────────────────────────────────────────────
# Set DATABASE_URL env var on the server to switch to PostgreSQL:
#   postgresql://cms_user:password@localhost:5432/cms_db
# Leave unset to use SQLite locally (development / single laptop).

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # PostgreSQL — for production server with multiple users
    print(f"Using PostgreSQL: {DATABASE_URL.split('@')[-1]}")  # hide credentials
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
else:
    # SQLite — for local development
    BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
    DB_PATH = BASE_DIR / "database" / "cms.db"
    DB_PATH.parent.mkdir(exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    print(f"Using SQLite: {DB_PATH}")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations():
    """Safely add new columns to existing tables without wiping data.
    Safe to call on every startup — silently skips already-existing columns."""
    new_columns = [
        "ALTER TABLE candidates ADD COLUMN must_have_missing    TEXT DEFAULT '[]'",
        "ALTER TABLE candidates ADD COLUMN nice_to_have_missing TEXT DEFAULT '[]'",
        "ALTER TABLE candidates ADD COLUMN project_suggestions  TEXT DEFAULT '[]'",
        "ALTER TABLE candidates ADD COLUMN align_history        TEXT DEFAULT '[]'",
        "ALTER TABLE candidates ADD COLUMN detected_domain      TEXT DEFAULT ''",
        "ALTER TABLE candidates ADD COLUMN injection_supported  INTEGER DEFAULT 1",
        "ALTER TABLE candidates ADD COLUMN soft_gaps            TEXT DEFAULT '[]'",
        "ALTER TABLE candidates ADD COLUMN semantic_score       REAL",
        "ALTER TABLE candidates ADD COLUMN structured_resume_json TEXT",
        "ALTER TABLE candidates ADD COLUMN candidate_function    TEXT DEFAULT ''",
        "ALTER TABLE candidates ADD COLUMN jd_function           TEXT DEFAULT ''",
        "ALTER TABLE candidates ADD COLUMN role_compatibility     TEXT DEFAULT ''",
        "ALTER TABLE candidates ADD COLUMN role                  TEXT DEFAULT ''",
        "ALTER TABLE candidates ADD COLUMN recruiter_name        TEXT DEFAULT ''",
        "ALTER TABLE client_requirements ADD COLUMN recruiter_name TEXT DEFAULT ''",
    ]
    with engine.begin() as conn:
        for sql in new_columns:
            try:
                conn.execute(text(sql))
            except Exception:
                pass  # column already exists — safe to ignore