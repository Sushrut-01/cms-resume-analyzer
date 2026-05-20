from fastapi import APIRouter
import requests
import ai_config
from services.llm_service import get_domain_coverage
from services.semantic_service import is_available as sem_available, get_tier as sem_tier

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("/")
def get_settings():
    cfg = ai_config.load()
    for field, alias in [
        ("gemini_api_key", "gemini_api_key_masked"),
        ("azure_api_key",  "azure_api_key_masked"),
        ("groq_api_key",   "groq_api_key_masked"),
        ("nvidia_api_key", "nvidia_api_key_masked"),
    ]:
        v = cfg.get(field, "")
        cfg[alias] = f"{v[:6]}...{v[-4:]}" if len(v) > 10 else ("set" if v else "")
        cfg.pop(field)
    return {"success": True, "data": cfg}


@router.post("/")
def update_settings(payload: dict):
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


@router.post("/test")
def test_connection():
    cfg = ai_config.load()
    provider = cfg.get("provider", "ollama")

    if provider == "ollama":
        try:
            r = requests.get(f"{cfg['ollama_url']}/api/tags", timeout=5)
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                return {"success": True, "provider": "ollama", "message": f"Connected. Models: {', '.join(models[:5]) or 'none found'}"}
            return {"success": False, "provider": "ollama", "message": f"Ollama returned HTTP {r.status_code}"}
        except Exception as e:
            return {"success": False, "provider": "ollama", "message": str(e)}

    elif provider == "gemini":
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


@router.get("/domain-coverage")
def domain_coverage():
    """Returns current domain coverage — updates automatically as new domains are added to code."""
    return {"success": True, "data": get_domain_coverage()}


@router.get("/admin-contact")
def admin_contact():
    cfg = ai_config.load()
    return {"success": True, "email": cfg.get("admin_contact_email", "")}


@router.get("/semantic-status")
def semantic_status():
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
