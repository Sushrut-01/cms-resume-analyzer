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
    # Mask all API keys before sending to the browser
    for field, alias in [
        ("gemini_api_key", "gemini_api_key_masked"),
        ("azure_api_key",  "azure_api_key_masked"),
        ("groq_api_key",   "groq_api_key_masked"),
        ("nvidia_api_key", "nvidia_api_key_masked"),
    ]:
        v = cfg.get(field, "")
        cfg[alias] = f"{v[:6]}...{v[-4:]}" if len(v) > 10 else ("set" if v else "")
        cfg.pop(field)  # Remove the real key from the response entirely
    return {"success": True, "data": cfg}


# -----------------------------------------------------------------------------
# POST /settings/ — Update AI Provider Configuration
#
# Accepts a whitelist of allowed fields only — unknown fields are silently ignored.
# This prevents accidental or malicious writes to unintended config keys.
# Saved to ai_config.json on disk (gitignored, not in the codebase).
# -----------------------------------------------------------------------------
@router.post("/")
def update_settings(payload: dict, _=Depends(require_admin)):
    # Only these specific keys are accepted — anything else is ignored
    allowed = {
        "provider", "ollama_url", "ollama_model",
        "gemini_api_key", "gemini_model",
        "azure_endpoint", "azure_api_key", "azure_deployment", "azure_api_version",
        "groq_api_key", "groq_model",
        "nvidia_api_key", "nvidia_model",
        "admin_contact_email",
    }
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
    provider = cfg.get("provider", "ollama")

    if provider == "ollama":
        # Ollama runs locally — check if the local service is running
        try:
            r = requests.get(f"{cfg['ollama_url']}/api/tags", timeout=5)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                return {"success": True, "provider": "ollama", "message": f"Connected. Models: {', '.join(models[:5]) or 'none found'}"}
            return {"success": False, "provider": "ollama", "message": f"Ollama returned HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "provider": "ollama", "message": str(e)}

    elif provider == "gemini":
        # Gemini is Google's cloud AI — requires an API key
        api_key = cfg.get("gemini_api_key", "")
        model   = cfg.get("gemini_model", "gemini-1.5-flash")
        if not api_key:
            return {"success": False, "provider": "gemini", "message": "No API key saved"}
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            body = {"contents": [{"parts": [{"text": "Hello"}]}],
                    "generationConfig": {"maxOutputTokens": 5}}
            r = requests.post(url, json=body, timeout=15)
            if r.status_code == 200:
                return {"success": True, "provider": "gemini", "message": f"Gemini API connected. Model: {model}"}
            err = r.json().get("error", {}).get("message", r.text[:120])
            return {"success": False, "provider": "gemini", "message": err}
        except Exception as e:
            return {"success": False, "provider": "gemini", "message": str(e)}

    elif provider == "azure":
        # Azure OpenAI — Kforce's production AI provider
        # Requires endpoint URL (from Azure portal) and API key
        endpoint   = cfg.get("azure_endpoint", "").rstrip("/")
        api_key    = cfg.get("azure_api_key", "")
        deployment = cfg.get("azure_deployment", "gpt-4o")
        api_ver    = cfg.get("azure_api_version", "2024-08-01-preview")
        if not endpoint or not api_key:
            return {"success": False, "provider": "azure", "message": "Azure endpoint or API key not configured"}
        try:
            url     = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_ver}"
            headers = {"api-key": api_key, "Content-Type": "application/json"}
            body    = {"messages": [{"role": "user", "content": "Hello"}], "max_tokens": 5, "temperature": 0}
            r = requests.post(url, headers=headers, json=body, timeout=15)
            if r.status_code == 200:
                return {"success": True, "provider": "azure", "message": f"Azure OpenAI connected. Deployment: {deployment}"}
            err = r.json().get("error", {}).get("message", r.text[:120])
            return {"success": False, "provider": "azure", "message": err}
        except Exception as e:
            return {"success": False, "provider": "azure", "message": str(e)}

    elif provider == "groq":
        # Groq is a fast cloud inference API — requires an API key
        api_key = cfg.get("groq_api_key", "")
        model   = cfg.get("groq_model", "llama-3.3-70b-versatile")
        if not api_key:
            return {"success": False, "provider": "groq", "message": "No API key saved"}
        try:
            url     = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            body    = {"model": model, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 5, "temperature": 0}
            r = requests.post(url, headers=headers, json=body, timeout=15)
            if r.status_code == 200:
                return {"success": True, "provider": "groq", "message": f"Groq connected. Model: {model}"}
            err = r.json().get("error", {}).get("message", r.text[:120])
            return {"success": False, "provider": "groq", "message": err}
        except Exception as e:
            return {"success": False, "provider": "groq", "message": str(e)}

    elif provider == "nvidia":
        # NVIDIA NIM — cloud inference using NVIDIA's API
        api_key = cfg.get("nvidia_api_key", "")
        model   = cfg.get("nvidia_model", "meta/llama-3.1-70b-instruct")
        if not api_key:
            return {"success": False, "provider": "nvidia", "message": "No API key saved"}
        try:
            url     = "https://integrate.api.nvidia.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            body    = {"model": model, "messages": [{"role": "user", "content": "Hello"}], "max_tokens": 5, "temperature": 0}
            r = requests.post(url, headers=headers, json=body, timeout=15)
            if r.status_code == 200:
                return {"success": True, "provider": "nvidia", "message": f"NVIDIA NIM connected. Model: {model}"}
            err = r.json().get("error", {}).get("message", r.text[:120])
            return {"success": False, "provider": "nvidia", "message": err}
        except Exception as e:
            return {"success": False, "provider": "nvidia", "message": str(e)}

    return {"success": False, "message": "Unknown provider"}


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
