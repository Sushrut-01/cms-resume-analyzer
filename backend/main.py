# =============================================================================
# main.py — Application Entry Point
#
# This is the first file that runs when the server starts.
# It does three things:
#   1. Configures the FastAPI application (security, CORS, middleware)
#   2. Sets up the database (creates tables, seeds default roles and admin)
#   3. Registers all API routers so endpoints are accessible
#
# To start the server:
#   uvicorn main:app --host 0.0.0.0 --port 8000
#
# Two URLs served:
#   /             → login page (index.html)
#   /static/...   → main app (preview.html) + static assets
# =============================================================================

# Load .env file first — all os.getenv() calls in other files depend on this
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
# These imports ensure SQLAlchemy creates the tables on startup even if the
# routers don't import them directly
import models.recruiter
import models.user
import models.role
from database import engine, Base, run_migrations, seed_roles, seed_admin

# -----------------------------------------------------------------------------
# Development mode flag
# When APP_ENV=development (default): Swagger docs (/docs) are enabled
# When APP_ENV=production: Swagger docs are hidden (not exposed to users)
# -----------------------------------------------------------------------------
_dev_mode = os.getenv("APP_ENV", "development").lower() == "development"

# -----------------------------------------------------------------------------
# Audit Logger
# Every HTTP request is logged to audit.log with:
#   IP address | user ID (from JWT) | method | path | status code | response time
# This gives IT a full record of who accessed what and when.
# Log file location: backend/audit.log (next to main.py)
# -----------------------------------------------------------------------------
_audit_log_path = Path(__file__).resolve().parent / "audit.log"
_audit_logger   = logging.getLogger("audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
_fh = logging.FileHandler(_audit_log_path, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(message)s"))
_audit_logger.addHandler(_fh)

# Create the FastAPI application
# Swagger UI is only accessible in development mode
app = FastAPI(
    title="K Recruit API",
    version="1.0.0",
    docs_url="/docs" if _dev_mode else None,
    redoc_url="/redoc" if _dev_mode else None,
    openapi_url="/openapi.json" if _dev_mode else None,
)


# -----------------------------------------------------------------------------
# Middleware 1: Audit Logging
# Runs on EVERY request. Logs to audit.log AFTER the response is sent.
# Extracts user_id from JWT token (best effort — doesn't re-validate the token).
# Format: <ip> user=<id> <METHOD> <path> -> <status> (<ms>ms)
# Example: 192.168.1.50 user=3 POST /candidates/upload -> 200 (412ms)
# -----------------------------------------------------------------------------
@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start    = time.time()
    response = await call_next(request)
    duration = round((time.time() - start) * 1000)

    # Extract user ID from the JWT token header — purely for audit logging
    user_id = "-"
    try:
        from jose import jwt as _jwt
        from deps import SECRET_KEY, ALGORITHM
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            payload = _jwt.decode(auth_header[7:], SECRET_KEY, algorithms=[ALGORITHM])
            user_id = payload.get("sub", "-")
    except Exception:
        pass  # unauthenticated requests log user="-"

    _audit_logger.info(
        f"{request.client.host if request.client else '-'} user={user_id} "
        f"{request.method} {request.url.path} -> {response.status_code} ({duration}ms)"
    )
    return response


# -----------------------------------------------------------------------------
# Middleware 2: Security Headers
# Added to every HTTP response to protect against common web attacks:
#
#   X-Content-Type-Options: nosniff
#     → Prevents browsers from guessing file types (MIME sniffing attacks)
#
#   X-Frame-Options: DENY
#     → Prevents the app from being embedded in an iframe (clickjacking attacks)
#
#   Referrer-Policy: strict-origin-when-cross-origin
#     → Limits what URL info is shared when navigating to external sites
#
#   Permissions-Policy: camera=(), microphone=(), geolocation=()
#     → Blocks browser features not needed by this app
#
#   Strict-Transport-Security (production only)
#     → Forces HTTPS for 1 year — only added when running behind nginx/SSL
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# CORS (Cross-Origin Resource Sharing)
# Controls which origins (URLs) are allowed to make API calls to this server.
# Set CORS_ORIGINS in .env — comma-separated list of allowed origins.
# Default: localhost only (development). Production: add the server's IP.
#
# Why this matters: Without CORS restrictions, any website could make API
# calls to K Recruit from a user's browser while they are logged in.
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# Database Initialization (runs once at startup)
#
#   create_all  → creates any missing tables (safe — does not drop existing data)
#   run_migrations → adds new columns to existing tables without wiping data
#   seed_roles  → creates super_admin, admin, recruiter roles if not present
#   seed_admin  → creates the first super_admin account if no users exist
# -----------------------------------------------------------------------------
Base.metadata.create_all(bind=engine)
run_migrations()
seed_roles()
seed_admin()

# -----------------------------------------------------------------------------
# Semantic Model Warmup (background thread)
# Loads the sentence-transformers model (~22 MB) in the background at startup.
# This avoids a delay on the first resume analysis request.
# Runs in a daemon thread so it doesn't block the server from starting.
# -----------------------------------------------------------------------------
def _warmup_semantic():
    try:
        from services.semantic_service import warmup
        warmup()
    except Exception:
        pass

threading.Thread(target=_warmup_semantic, daemon=True).start()

# -----------------------------------------------------------------------------
# API Routers
# Each router handles a group of related endpoints.
# All routes are prefixed as defined in each router file.
# -----------------------------------------------------------------------------
app.include_router(auth.router)              # /auth/*
app.include_router(users.router)             # /users/*
app.include_router(roles.router)             # /roles/*
app.include_router(candidates.router)        # /candidates/*
app.include_router(client_requirements.router)  # /client-requirements/*
app.include_router(settings.router)          # /settings/*
app.include_router(recruiters.router)        # /recruiters/*
app.include_router(bot.router)               # /bot/*

# -----------------------------------------------------------------------------
# Frontend Static Files
# The React frontend is served as static HTML files (no separate Node server).
#   /static/*    → serves all files in backend/static/ folder
#   /            → serves index.html (login page)
# After login, the browser is redirected to /static/preview.html (main app).
# -----------------------------------------------------------------------------
BASE_DIR   = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}

@app.get("/")
def serve_frontend():
    return FileResponse(STATIC_DIR / "index.html", headers=_NO_CACHE)

@app.get("/static/preview.html")
def serve_preview():
    return FileResponse(STATIC_DIR / "preview.html", headers=_NO_CACHE)

# -----------------------------------------------------------------------------
# Health Check
# GET /health → {"status": "ok"}
# Used by monitoring tools or load balancers to confirm the server is running.
# Does not require authentication.
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}
