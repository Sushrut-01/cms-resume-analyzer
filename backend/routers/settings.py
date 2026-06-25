# =============================================================================
# settings.py — AI Provider Configuration & Status
#
# Manages which AI provider is used for resume analysis (Ollama, Azure, Groq, etc.)
# and allows testing the connection to the configured provider.
#
# Access levels:
#   GET  /settings/              → require_admin     (view AI configuration)
#   POST /settings/              → require_admin     (change AI provider/keys)
#   POST /settings/test          → require_admin     (test live AI connection)
#   GET  /settings/domain-coverage → get_current_user (any user can view domains)
#   GET  /settings/admin-contact   → get_current_user (any user can view contact)
#   GET  /settings/semantic-status → get_current_user (any user can view model status)
#
# SECURITY: API keys are stored in ai_config.json (gitignored).
# When returned to the browser, keys are MASKED (first 6 + last 4 chars shown).
# Full key values never leave the server after being saved.
# =============================================================================

from fastapi import APIRouter, Depends
import requests
import psutil
import ai_config
from deps import get_current_user, require_admin
from services.llm_service import get_domain_coverage
from services.semantic_service import is_available as sem_available, get_tier as sem_tier

router = APIRouter(prefix="/settings", tags=["Settings"])


# -----------------------------------------------------------------------------
# GET /settings/ — Retrieve Current AI Configuration
#
# Returns all saved settings, but with API keys MASKED for security.
# Example: "gsk_aBcDeFgHiJ...XyZ1" instead of the full key.
# This way admins can confirm a key is set without seeing the actual value.
# -----------------------------------------------------------------------------
@router.get("/")
def get_settings(_=Depends(require_admin)):
    cfg = ai_config.load()
    # Only return Ollama-relevant fields
    return {"success": True, "data": {
        "provider":            "ollama",
        "ollama_url":          cfg.get("ollama_url", "http://localhost:11434"),
        "ollama_model":        cfg.get("ollama_model", "qwen2.5:7b"),
        "admin_contact_email": cfg.get("admin_contact_email", ""),
    }}


# -----------------------------------------------------------------------------
# POST /settings/ — Update AI Provider Configuration
#
# Accepts a whitelist of allowed fields only — unknown fields are silently ignored.
# This prevents accidental or malicious writes to unintended config keys.
# Saved to ai_config.json on disk (gitignored, not in the codebase).
# -----------------------------------------------------------------------------
@router.post("/")
def update_settings(payload: dict, _=Depends(require_admin)):
    allowed = {"provider", "ollama_url", "ollama_model", "admin_contact_email"}
    updates = {k: v for k, v in payload.items() if k in allowed}
    ai_config.save(updates)
    return {"success": True}


# -----------------------------------------------------------------------------
# POST /settings/test — Test Live AI Provider Connection
#
# Sends a minimal "Hello" message to the configured AI provider.
# Used to verify that the API key and endpoint are working correctly.
# Each provider has a different API format — handled separately below.
# Returns success=True with the connected model/deployment name on success.
# Returns success=False with the error message on failure.
# No resume data is sent — only a single test word.
# -----------------------------------------------------------------------------
@router.post("/test")
def test_connection(_=Depends(require_admin)):
    cfg = ai_config.load()
    try:
        r = requests.get(f"{cfg.get('ollama_url', 'http://localhost:11434')}/api/tags", timeout=5)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return {"success": True, "provider": "ollama", "message": f"Connected. Models: {', '.join(models[:5]) or 'none pulled yet'}"}
        return {"success": False, "provider": "ollama", "message": f"Ollama returned HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "provider": "ollama", "message": str(e)}


# -----------------------------------------------------------------------------
# GET /settings/domain-coverage — View Supported Resume Domains
# Returns the list of job domains the AI can detect and analyze.
# Example: IT, Finance, Healthcare, Engineering, etc.
# Read-only — any logged-in user can view this.
# -----------------------------------------------------------------------------
@router.get("/domain-coverage")
def domain_coverage(_=Depends(get_current_user)):
    """Returns current domain coverage — updates automatically as new domains are added to code."""
    return {"success": True, "data": get_domain_coverage()}


# -----------------------------------------------------------------------------
# GET /settings/admin-contact — Get Admin Contact Email
# Returns the admin/support email address configured in settings.
# Shown to recruiters when they need help (e.g. account issues).
# -----------------------------------------------------------------------------
@router.get("/admin-contact")
def admin_contact(_=Depends(get_current_user)):
    cfg = ai_config.load()
    return {"success": True, "email": cfg.get("admin_contact_email", "")}


# -----------------------------------------------------------------------------
# GET /settings/semantic-status — Check Semantic Scoring Model Status
#
# Semantic scoring compares resumes to JDs using AI embeddings (not just keywords).
# Two tiers:
#   "st"    = sentence-transformers — neural embeddings, best quality
#   "tfidf" = scikit-learn TF-IDF — text overlap, lightweight fallback
#   "none"  = model not installed
# This endpoint tells the UI which tier is active so it can show the right label.
# -----------------------------------------------------------------------------
@router.get("/semantic-status")
def semantic_status(_=Depends(get_current_user)):
    available = sem_available()
    tier      = sem_tier()
    tier_labels = {
        "st":    "sentence-transformers (neural embeddings — best quality)",
        "tfidf": "scikit-learn TF-IDF (text overlap — lightweight fallback)",
        "none":  "Not available — install sentence-transformers or scikit-learn",
    }
    return {
        "available": available,
        "tier":      tier,
        "label":     tier_labels.get(tier, tier),
        "install":   "pip install sentence-transformers" if not available else None,
    }


# -----------------------------------------------------------------------------
# GET /settings/system-status — RAM, active Ollama model, installed models
# Polled every 2s by the Model Manager UI for real-time RAM display.
# -----------------------------------------------------------------------------
@router.get("/system-status")
def system_status(_=Depends(require_admin)):
    cfg = ai_config.load()
    ollama_url = cfg.get("ollama_url", "http://localhost:11434")

    # RAM from OS
    ram = psutil.virtual_memory()
    ram_total_gb = round(ram.total / 1e9, 1)
    ram_used_gb  = round(ram.used  / 1e9, 1)
    ram_free_gb  = round(ram.available / 1e9, 1)
    ram_percent  = ram.percent

    # Currently loaded model in Ollama (from /api/ps — shows running models)
    loaded_model = None
    try:
        ps = requests.get(f"{ollama_url}/api/ps", timeout=3)
        if ps.status_code == 200:
            models_running = ps.json().get("models", [])
            if models_running:
                loaded_model = models_running[0].get("name", "")
    except Exception:
        pass

    # All installed models (downloaded, not necessarily running)
    installed = []
    try:
        tags = requests.get(f"{ollama_url}/api/tags", timeout=3)
        if tags.status_code == 200:
            installed = [
                {
                    "name": m.get("name", ""),
                    "size_gb": round(m.get("size", 0) / 1e9, 1),
                }
                for m in tags.json().get("models", [])
            ]
    except Exception:
        pass

    return {
        "ram": {
            "total_gb":  ram_total_gb,
            "used_gb":   ram_used_gb,
            "free_gb":   ram_free_gb,
            "percent":   ram_percent,
        },
        "loaded_model": loaded_model,
        "installed":    installed,
        "active_config": cfg.get("ollama_model", ""),
    }


# -----------------------------------------------------------------------------
# GET /settings/ai-logs — Last N AI call log entries
# Returns parsed log lines for the admin log viewer in Settings UI.
# -----------------------------------------------------------------------------
@router.get("/ai-logs")
def ai_logs(limit: int = 100, _=Depends(require_admin)):
    import os
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ai_calls.log")
    if not os.path.exists(log_path):
        return {"success": True, "logs": [], "total": 0}

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Parse each line into a dict for the frontend
    parsed = []
    for line in reversed(lines[-500:]):  # last 500, newest first
        line = line.strip()
        if not line:
            continue
        parts = dict(p.split("=", 1) for p in line.split(" | ") if "=" in p)
        ts = line.split(" | ")[0] if line else ""
        parsed.append({
            "ts":           ts,
            "user_id":      parts.get("user_id", "—"),
            "email":        parts.get("email", "—"),
            "candidate_id": parts.get("candidate_id", "—"),
            "op":           parts.get("op", "—"),
            "model":        parts.get("model", "—"),
            "duration":     parts.get("duration", "—"),
            "status":       parts.get("status", "—"),
            "extra":        parts.get("tokens", "") or parts.get("error", ""),
        })
        if len(parsed) >= limit:
            break

    return {"success": True, "logs": parsed, "total": len(lines)}


# -----------------------------------------------------------------------------
# Ollama proxy endpoints — browser can't call Ollama directly (CORS).
# These endpoints proxy model management calls server-side.
# -----------------------------------------------------------------------------

@router.get("/ollama/tags")
def ollama_tags(_=Depends(require_admin)):
    cfg = ai_config.load()
    url = cfg.get("ollama_url", "http://localhost:11434")
    try:
        r = requests.get(f"{url}/api/tags", timeout=5)
        return r.json()
    except Exception as e:
        return {"models": [], "error": str(e)}


@router.post("/ollama/pull")
def ollama_pull(payload: dict, _=Depends(require_admin)):
    cfg = ai_config.load()
    url = cfg.get("ollama_url", "http://localhost:11434")
    model = payload.get("name", "")
    if not model:
        return {"success": False, "error": "Model name required"}
    try:
        r = requests.post(f"{url}/api/pull",
                          json={"name": model, "stream": False},
                          timeout=1800)  # 30 min timeout for large models
        if r.status_code == 200:
            # Save new model as active
            ai_config.save({"ollama_model": model})
            return {"success": True, "model": model}
        return {"success": False, "error": f"Ollama returned HTTP {r.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/ollama/delete")
def ollama_delete(payload: dict, _=Depends(require_admin)):
    cfg = ai_config.load()
    url = cfg.get("ollama_url", "http://localhost:11434")
    model = payload.get("name", "")
    try:
        r = requests.delete(f"{url}/api/delete",
                            json={"name": model},
                            timeout=30)
        return {"success": r.status_code in (200, 404)}
    except Exception as e:
        return {"success": False, "error": str(e)}
