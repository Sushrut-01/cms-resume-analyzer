from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from collections import defaultdict
import time
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
    if not user or not _verify(password, user.password_hash):
        _record_failure(ip)
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
