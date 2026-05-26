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
        "permissions": _ALL_PAGES + _ALL_ACTIONS,
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


def seed_roles():
    """Seed the three system roles. Runs once; promotes any existing admin → super_admin."""
    try:
        import json
        from models.role import Role
        from models.user import User

        db = SessionLocal()
        try:
            if db.query(Role).count() > 0:
                return  # already seeded
            for name, cfg in _SYSTEM_ROLES.items():
                db.add(Role(
                    name        = name,
                    label       = cfg["label"],
                    permissions = json.dumps(cfg["permissions"]),
                    is_system   = True,
                ))
            db.flush()
            # Promote existing admin users (seeded before roles existed) to super_admin
            db.query(User).filter(User.role == "admin").update({"role": "super_admin"})
            db.commit()
            print("[auth] Seeded default roles: super_admin, admin, recruiter")
        finally:
            db.close()
    except Exception as e:
        print(f"[auth] Warning: seed_roles skipped: {e}")


def seed_admin():
    """Create the first super_admin account from ai_config credentials if no users exist yet."""
    try:
        from models.user import User
        from passlib.context import CryptContext
        import ai_config

        db = SessionLocal()
        try:
            if db.query(User).count() == 0:
                cfg      = ai_config.load()
                email    = cfg.get("login_username", "admin")
                password = cfg.get("login_password", "cms@2024")
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