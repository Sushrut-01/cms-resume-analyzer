from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from pathlib import Path
import threading

from routers import candidates, client_requirements, settings, recruiters, auth
import models.recruiter  # ensure table is created by create_all
from database import engine, Base, run_migrations

# ──────────────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────────────
load_dotenv()

app = FastAPI(
    title="K Recruit API",
    version="1.0.0",
)

# ──────────────────────────────────────────────────────
# CORS
# ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)
run_migrations()   # safely add new columns to existing DB

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
app.include_router(candidates.router)
app.include_router(client_requirements.router)
app.include_router(settings.router)
app.include_router(recruiters.router)

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
    return FileResponse(STATIC_DIR / "index.html")

# ──────────────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}