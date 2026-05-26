"""Central config store for AI provider settings. Persisted to ai_config.json."""
import json
import os

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_config.json")

_DEFAULT = {
    "provider":          "ollama",      # "ollama" | "gemini" | "azure" | "groq" | "nvidia"
    "ollama_url":        "http://localhost:11434",
    "ollama_model":      "qwen2.5:0.5b",
    "gemini_api_key":    "",
    "gemini_model":      "gemini-1.5-flash",
    "azure_endpoint":       "",           # e.g. https://YOUR-RESOURCE.openai.azure.com
    "azure_api_key":        "",
    "azure_deployment":     "gpt-4o",
    "azure_api_version":    "2024-08-01-preview",
    "groq_api_key":         "",
    "groq_model":           "meta-llama/llama-4-scout-17b-16e-instruct",
    "nvidia_api_key":       "",
    "nvidia_model":         "meta/llama-3.1-70b-instruct",
    "admin_contact_email":  "",           # shown to users when a domain limitation is hit
    "login_username":       "admin",
    "login_password":       "",          # set ADMIN_PASSWORD in .env — never hardcode here
}


def load() -> dict:
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULT, **data}
        except Exception:
            pass
    return _DEFAULT.copy()


def save(updates: dict) -> dict:
    current = load()
    current.update(updates)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current
