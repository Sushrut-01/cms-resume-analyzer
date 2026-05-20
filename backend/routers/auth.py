from fastapi import APIRouter, HTTPException
import hashlib
import ai_config

router = APIRouter(prefix="/auth", tags=["Auth"])

_SECRET = "cms-talent-ai-2024"


def _make_token(username: str, password: str) -> str:
    return hashlib.sha256(f"{username}:{password}:{_SECRET}".encode()).hexdigest()


@router.post("/login")
def login(payload: dict):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    cfg = ai_config.load()
    if username != cfg.get("login_username") or password != cfg.get("login_password"):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _make_token(username, password)
    return {"success": True, "token": token, "username": username}


@router.post("/verify")
def verify(payload: dict):
    token = payload.get("token") or ""
    cfg = ai_config.load()
    expected = _make_token(cfg["login_username"], cfg["login_password"])
    if token != expected:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return {"valid": True, "username": cfg["login_username"]}
