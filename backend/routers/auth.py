from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from collections import defaultdict
import time
import os
from database import get_db
from deps import get_current_user, SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/auth", tags=["Auth"])

# Simple in-memory brute-force guard: max 5 failed attempts per IP per 60 seconds
_failed_attempts: dict[str, list[float]] = defaultdict(list)
_WINDOW_SECONDS = 60
_MAX_ATTEMPTS   = 5


def _check_rate_limit(ip: str):
    now  = time.time()
    hits = [t for t in _failed_attempts[ip] if now - t < _WINDOW_SECONDS]
    _failed_attempts[ip] = hits
    if len(hits) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {_WINDOW_SECONDS} seconds.",
        )


def _record_failure(ip: str):
    _failed_attempts[ip].append(time.time())


def _clear_failures(ip: str):
    _failed_attempts.pop(ip, None)


def _hash(plain: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(plain)


def _verify(plain: str, hashed: str) -> bool:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").verify(plain, hashed)


def _make_token(user_id: int, role: str) -> str:
    from jose import jwt
    exp = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def _get_permissions(db, role_name: str) -> list:
    import json
    from models.role import Role
    role = db.query(Role).filter(Role.name == role_name).first()
    return json.loads(role.permissions) if role else []


@router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    from models.user import User
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    email    = (payload.get("email") or payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not user.password_hash or not _verify(password, user.password_hash):
        _record_failure(ip)
        if user and not user.password_hash:
            raise HTTPException(status_code=401, detail="This account uses Microsoft sign-in. Please use the 'Sign in with Microsoft' button.")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    _clear_failures(ip)
    token = _make_token(user.id, user.role)
    return {
        "success":     True,
        "token":       token,
        "username":    user.name,
        "role":        user.role,
        "user_id":     user.id,
        "permissions": _get_permissions(db, user.role),
    }


@router.post("/verify")
def verify(payload: dict, db: Session = Depends(get_db)):
    from models.user import User
    from jose import jwt, JWTError
    token = payload.get("token") or ""
    try:
        data    = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = data.get("sub")
        if not user_id:
            raise ValueError
    except Exception:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")

    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return {
        "valid":       True,
        "username":    user.name,
        "role":        user.role,
        "user_id":     user.id,
        "permissions": _get_permissions(db, user.role),
    }


@router.get("/me")
def me(current_user=Depends(get_current_user)):
    return {
        "id":    current_user.id,
        "name":  current_user.name,
        "email": current_user.email,
        "role":  current_user.role,
    }


@router.get("/entra-config")
def entra_config():
    """Return Entra ID client_id and tenant_id for MSAL.js. Empty strings if not configured."""
    return {
        "client_id": os.getenv("ENTRA_CLIENT_ID", ""),
        "tenant_id": os.getenv("ENTRA_TENANT_ID", ""),
    }


@router.post("/microsoft")
def microsoft_login(payload: dict, db: Session = Depends(get_db)):
    """Validate Microsoft Entra ID token, look up user by email, return K Recruit JWT."""
    from models.user import User
    import requests as _req

    id_token = payload.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="id_token is required")

    tenant_id = os.getenv("ENTRA_TENANT_ID", "")
    client_id = os.getenv("ENTRA_CLIENT_ID", "")
    if not tenant_id or not client_id:
        raise HTTPException(status_code=503, detail="Microsoft login is not configured on this server.")

    # Validate the Microsoft token using Microsoft's public JWKS
    try:
        from jose import jwt as _jwt, jwk, JWTError
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        jwks_resp = _req.get(jwks_url, timeout=10)
        jwks_resp.raise_for_status()
        jwks_data = jwks_resp.json()

        # Decode without verification first to get the key id
        unverified = _jwt.get_unverified_header(id_token)
        kid = unverified.get("kid")
        key = next((k for k in jwks_data["keys"] if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(status_code=401, detail="Microsoft token key not found.")

        claims = _jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            options={"verify_at_hash": False},
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid Microsoft token: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Could not validate Microsoft token.")

    # Extract email from token claims
    email = (
        claims.get("preferred_username") or
        claims.get("email") or
        claims.get("upn") or ""
    ).strip().lower()
    name = claims.get("name", email.split("@")[0].title())

    if not email:
        raise HTTPException(status_code=401, detail="Microsoft token did not contain an email address.")

    # Look up user in K Recruit database by email
    user = db.query(User).filter(
        User.email.ilike(email),
        User.is_active == True
    ).first()

    if not user:
        raise HTTPException(
            status_code=403,
            detail="Your account is not set up in K Recruit. Contact your admin."
        )

    token = _make_token(user.id, user.role)
    return {
        "success":     True,
        "token":       token,
        "username":    user.name or name,
        "role":        user.role,
        "user_id":     user.id,
        "permissions": _get_permissions(db, user.role),
    }
