# =============================================================================
# database.py — Database Connection & Initialization
#
# Handles everything related to the database:
#   1. Connecting to SQLite (development) or PostgreSQL (production)
#   2. Providing a database session to each API request
#   3. Running schema migrations safely (add new columns without data loss)
#   4. Seeding default roles and the first admin account
#
# How to switch from SQLite to PostgreSQL:
#   Set DATABASE_URL in .env:
#   DATABASE_URL=postgresql://cms_user:password@localhost:5432/cms_db
#   Leave DATABASE_URL unset to use SQLite (file-based, no server needed).
# =============================================================================

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
import pathlib
import os

# -----------------------------------------------------------------------------
# Database Connection
# SQLite: stored at database/cms.db relative to the project root
# PostgreSQL: connects to the server specified in DATABASE_URL
# -----------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # PostgreSQL — for production server with multiple concurrent users
    # pool_size=10: keep 10 persistent connections open (reused across requests)
    # max_overflow=20: allow up to 20 extra connections during traffic spikes
    print(f"Using PostgreSQL: {DATABASE_URL.split('@')[-1]}")  # hide credentials in logs
    engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
else:
    # SQLite — simple file database for local development or single-laptop use
    # check_same_thread=False: allows the same connection to be used across threads
    BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
    DB_PATH  = BASE_DIR / "database" / "cms.db"
    DB_PATH.parent.mkdir(exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    print(f"Using SQLite: {DB_PATH}")
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# Session factory — creates a new DB session for each API request
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class — all SQLAlchemy models (User, Candidate, etc.) inherit from this
Base = declarative_base()


# -----------------------------------------------------------------------------
# get_db — Database Session Provider
# Used by FastAPI's dependency injection (Depends(get_db)) in every router.
# Opens a session at the start of each request and closes it when done.
# The "finally" block ensures the session is always closed, even on errors.
# -----------------------------------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -----------------------------------------------------------------------------
# run_migrations — Safe Schema Updates
#
# Adds new columns to existing tables without dropping or recreating them.
# This preserves all existing data when the application is updated.
# Safe to run on every startup — silently ignores columns that already exist.
#
# Why this is needed: SQLAlchemy's create_all() only creates NEW tables.
# It does not modify existing tables. So when we add a new field to a model,
# we also add it here to ensure existing databases get the new column.
# -----------------------------------------------------------------------------
def run_migrations():
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
        # Data isolation: each candidate is linked to the user who uploaded them
        # recruiter role sees only their own; admins see all
        "ALTER TABLE candidates ADD COLUMN uploaded_by INTEGER DEFAULT NULL",
    ]
    with engine.begin() as conn:
        for sql in new_columns:
            try:
                conn.execute(text(sql))
            except Exception:
                pass  # column already exists — safe to ignore


# -----------------------------------------------------------------------------
# Role & Permission Definitions
# These are the three built-in system roles.
# They are created once on first startup and cannot be deleted.
#
# super_admin: full access to everything including role management
# admin:       full access except role management
# recruiter:   can upload, analyze, align, and download resumes only
# -----------------------------------------------------------------------------
_ALL_PAGES = [
    "page:dashboard", "page:jdmanager", "page:upload", "page:listing",
    "page:review", "page:jdalign", "page:jdalignlist", "page:pipeline",
    "page:settings", "page:users", "page:roles",
]
_ALL_ACTIONS = [
    "action:analyze_candidate", "action:approve_candidate", "action:delete_candidate",
    "action:create_jd", "action:edit_jd", "action:delete_jd",
    "action:generate_aligned", "action:download_resume",
    "action:manage_users", "action:manage_roles", "action:edit_settings",
]
_SYSTEM_ROLES = {
    "super_admin": {
        "label":       "Super Admin",
        "permissions": _ALL_PAGES + _ALL_ACTIONS,   # all pages + all actions
    },
    "admin": {
        "label":       "Admin",
        "permissions": [p for p in _ALL_PAGES if p != "page:roles"] +
                       [a for a in _ALL_ACTIONS if a != "action:manage_roles"],
    },
    "recruiter": {
        "label":       "Recruiter",
        "permissions": [
            "page:dashboard", "page:upload", "page:listing", "page:review",
            "page:jdalign", "page:jdalignlist", "page:pipeline",
            "action:analyze_candidate", "action:generate_aligned", "action:download_resume",
        ],
    },
}


# -----------------------------------------------------------------------------
# seed_roles — Create Default Roles on First Startup
# Runs once when the roles table is empty.
# Also promotes any existing "admin" users to "super_admin" if roles
# are being seeded for the first time on an existing database.
# -----------------------------------------------------------------------------
def seed_roles():
    try:
        import json
        from models.role import Role
        from models.user import User

        db = SessionLocal()
        try:
            if db.query(Role).count() > 0:
                return  # already seeded — skip
            for name, cfg in _SYSTEM_ROLES.items():
                db.add(Role(
                    name        = name,
                    label       = cfg["label"],
                    permissions = json.dumps(cfg["permissions"]),
                    is_system   = True,
                ))
            db.flush()
            # Promote any pre-existing admin accounts to super_admin
            db.query(User).filter(User.role == "admin").update({"role": "super_admin"})
            db.commit()
            print("[auth] Seeded default roles: super_admin, admin, recruiter")
        finally:
            db.close()
    except Exception as e:
        print(f"[auth] Warning: seed_roles skipped: {e}")


# -----------------------------------------------------------------------------
# seed_admin — Create the First Super Admin Account
# Only runs when the users table is completely empty (fresh install).
# Reads credentials from ADMIN_USERNAME and ADMIN_PASSWORD in .env.
# Password is bcrypt-hashed before storing — never saved as plain text.
# -----------------------------------------------------------------------------
def seed_admin():
    try:
        from models.user import User
        from passlib.context import CryptContext
        import ai_config

        db = SessionLocal()
        try:
            if db.query(User).count() == 0:
                cfg      = ai_config.load()
                import os as _os
                email    = cfg.get("login_username") or _os.getenv("ADMIN_USERNAME", "admin")
                password = cfg.get("login_password") or _os.getenv("ADMIN_PASSWORD", "cms@2024")
                name     = email.split("@")[0].title()
                ctx      = CryptContext(schemes=["bcrypt"], deprecated="auto")
                db.add(User(
                    name          = name,
                    email         = email,
                    password_hash = ctx.hash(password),
                    role          = "super_admin",
                ))
                db.commit()
                print(f"[auth] Seeded first super_admin: {email}")
        finally:
            db.close()
    except Exception as e:
        print(f"[auth] Warning: seed_admin skipped: {e}")
