from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import os
import time
import logging
import threading

from routers import candidates, client_requirements, settings, recruiters, auth, bot, users, roles
import models.recruiter  # ensure table is created by create_all
import models.user       # ensure users table is created by create_all
import models.role       # ensure roles table is created by create_all
from database import engine, Base, run_migrations, seed_roles, seed_admin

# ──────────────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────────────

_dev_mode = os.getenv("APP_ENV", "development").lower() == "development"

# ──────────────────────────────────────────────────────
# Audit logger — appends to audit.log next to main.py
# ──────────────────────────────────────────────────────
_audit_log_path = Path(__file__).resolve().parent / "audit.log"
_audit_logger   = logging.getLogger("audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
_fh = logging.FileHandler(_audit_log_path, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(message)s"))
_audit_logger.addHandler(_fh)

app = FastAPI(
    title="K Recruit API",
    version="1.0.0",
    docs_url="/docs" if _dev_mode else None,
    redoc_url="/redoc" if _dev_mode else None,
    openapi_url="/openapi.json" if _dev_mode else None,
)

# ──────────────────────────────────────────────────────
# Audit logging middleware
# ──────────────────────────────────────────────────────
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start    = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)

    # Extract bearer token subject (user id) without re-validating — best effort
    user_id = "-"
    try:
        from jose import jwt as _jwt
        from deps import SECRET_KEY, ALGORITHM
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            payload = _jwt.decode(auth_header[7:], SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub", "-")
    except Exception:
        pass

    _audit_logger.info(
        f"{request.client.host if request.client else '-'} user={user_id} "
        f"{request.method} {request.url.path} -> {response.status_code} ({duration}ms)"
    )
    return response


# ──────────────────────────────────────────────────────
# Security headers middleware
# ──────────────────────────────────────────────────────
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    # CSP is intentionally deferred until Babel is replaced (Option B/C in compliance doc).
    # Babel requires 'unsafe-eval' which defeats XSS protection — adding CSP now provides
    # false confidence. Proper CSP will be added when libraries are self-hosted (Section 6B).
    if not _dev_mode:
        # Only add HSTS when running over HTTPS (production with nginx)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ──────────────────────────────────────────────────────
# CORS — locked to same host; * removed for compliance
# ──────────────────────────────────────────────────────
_allowed_origins = os.getenv(
    "CORS_ORIGINS",
    "http://127.0.0.1:8000,http://localhost:8000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ──────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)
run_migrations()   # safely add new columns to existing DB
seed_roles()       # seed super_admin / admin / recruiter roles; promote admin → super_admin
seed_admin()       # create first super_admin from ai_config if users table is empty

# Warm up semantic model in background — downloads ~22 MB on first run
def _warmup_semantic():
    try:
        from services.semantic_service import warmup
        warmup()
    except Exception:
        pass

threading.Thread(target=_warmup_semantic, daemon=True).start()

# ──────────────────────────────────────────────────────
# API Routers
# ──────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(candidates.router)
app.include_router(client_requirements.router)
app.include_router(settings.router)
app.include_router(recruiters.router)
app.include_router(bot.router)

# ──────────────────────────────────────────────────────
# Frontend (static HTML with CDN React)
# ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount(
    "/static",
    StaticFiles(directory=STATIC_DIR),
    name="static",
)

@app.get("/")
def serve_frontend():
    # index.html = login page; after login with "premium" UI it redirects to /static/preview.html
    return FileResponse(STATIC_DIR / "index.html")

# ──────────────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}