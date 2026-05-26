# =============================================================================
# ai_config.py — AI Provider Configuration Store
#
# Stores and retrieves the active AI provider settings.
# Settings are persisted to ai_config.json on disk (gitignored).
#
# Why a separate JSON file instead of .env?
# .env is for secrets and server-level config (JWT_SECRET, DB URL).
# ai_config.json is for user-changeable settings that admins update
# through the Settings page in the UI — no server restart needed.
#
# Supported AI providers:
#   ollama  → local model running on the same laptop (no internet, no cost)
#   azure   → Azure OpenAI (GPT-4o) — Kforce production choice
#   gemini  → Google Gemini API
#   groq    → Groq cloud inference (fast, free tier available)
#   nvidia  → NVIDIA NIM API
#
# SECURITY: ai_config.json is gitignored — API keys never enter the git repo.
# When keys are returned to the browser via GET /settings/, they are masked.
# =============================================================================

import json
import os

# Path to the config file — stored next to this script in the backend folder
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_config.json")

# Default values used when no config file exists yet (fresh install)
# or when a key is missing from the saved config.
_DEFAULT = {
    "provider":          "ollama",       # active AI provider
    "ollama_url":        "http://localhost:11434",
    "ollama_model":      "qwen2.5:0.5b",
    "gemini_api_key":    "",
    "gemini_model":      "gemini-1.5-flash",
    "azure_endpoint":    "",             # e.g. https://YOUR-RESOURCE.openai.azure.com
    "azure_api_key":     "",
    "azure_deployment":  "gpt-4o",
    "azure_api_version": "2024-08-01-preview",
    "groq_api_key":      "",
    "groq_model":        "meta-llama/llama-4-scout-17b-16e-instruct",
    "nvidia_api_key":    "",
    "nvidia_model":      "meta/llama-3.1-70b-instruct",
    "admin_contact_email": "",           # shown to users when they need help
    "login_username":    "admin",
    "login_password":    "",             # set ADMIN_PASSWORD in .env — never hardcode here
}


# -----------------------------------------------------------------------------
# load — Read Current Configuration
# Returns the saved config merged with defaults.
# If ai_config.json doesn't exist or is corrupt, returns the default values.
# Merge order: defaults are overridden by whatever is saved in the file.
# -----------------------------------------------------------------------------
def load() -> dict:
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Merge: saved values override defaults, missing keys get defaults
            return {**_DEFAULT, **data}
        except Exception:
            pass  # corrupt file — fall through to return defaults
    return _DEFAULT.copy()


# -----------------------------------------------------------------------------
# save — Write Updated Settings to Disk
# Merges the updates into the current saved config and writes the full config.
# Only the specific keys in `updates` are changed — everything else is preserved.
# Returns the updated config dict.
# -----------------------------------------------------------------------------
def save(updates: dict) -> dict:
    current = load()
    current.update(updates)
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current
