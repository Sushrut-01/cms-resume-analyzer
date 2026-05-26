# =============================================================================
# auth.py — Login & Session Management
#
# Handles all authentication flows:
#   1. Email + Password login  (for users with a K Recruit password)
#   2. Microsoft Entra ID login (for Kforce employees via Microsoft account)
#   3. Token verification       (check if a saved session is still valid)
#   4. Entra ID config          (tell the browser whether to show MS button)
#
# After any successful login, this file issues a K Recruit JWT token.
# That token is then used by deps.py to protect every other endpoint.
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from collections import defaultdict
import time
import os
from database import get_db
from deps import get_current_user, SECRET_KEY, ALGORITHM

router = APIRouter(prefix="/auth", tags=["Auth"])

# -----------------------------------------------------------------------------
# Brute-force protection
# Tracks failed login attempts per IP address.
# Max 5 failed attempts within 60 seconds → block further attempts.
# Prevents automated password guessing attacks.
# -----------------------------------------------------------------------------
_failed_attempts: dict[str, list[float]] = defaultdict(list)
_WINDOW_SECONDS = 60   # sliding time window
_MAX_ATTEMPTS   = 5    # max failures before lockout


def _check_rate_limit(ip: str):
    # Keep only attempts within the last 60 seconds
    now  = time.time()
    hits = [t for t in _failed_attempts[ip] if now - t < _WINDOW_SECONDS]
    _failed_attempts[ip] = hits
    if len(hits) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in {_WINDOW_SECONDS} seconds.",
        )


def _record_failure(ip: str):
    # Add a timestamp for this failed attempt
    _failed_attempts[ip].append(time.time())


def _clear_failures(ip: str):
    # Successful login clears the failure history for this IP
    _failed_attempts.pop(ip, None)


# -----------------------------------------------------------------------------
# Helper: Hash a plain-text password using bcrypt
# Passwords are NEVER stored as plain text in the database.
# bcrypt is a one-way hash — you cannot reverse it to get the original password.
# -----------------------------------------------------------------------------
def _hash(plain: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(plain)


# -----------------------------------------------------------------------------
# Helper: Verify a plain-text password against a stored bcrypt hash
# Returns True if they match, False otherwise.
# -----------------------------------------------------------------------------
def _verify(plain: str, hashed: str) -> bool:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"], deprecated="auto").verify(plain, hashed)


# -----------------------------------------------------------------------------
# Helper: Create a JWT token for a successfully authenticated user
# The token contains: user_id, role, and expiry (24 hours from now)
# Signed with JWT_SECRET so it cannot be forged.
# This same token is issued for BOTH password login and Microsoft login.
# -----------------------------------------------------------------------------
def _make_token(user_id: int, role: str) -> str:
    from jose import jwt
    exp = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


# -----------------------------------------------------------------------------
# Helper: Fetch the list of permissions for a given role from the database
# Permissions are stored as a JSON array in the roles table.
# Example: ["upload_resume", "analyze", "view_candidates"]
# -----------------------------------------------------------------------------
def _get_permissions(db, role_name: str) -> list:
    import json
    from models.role import Role
    role = db.query(Role).filter(Role.name == role_name).first()
    return json.loads(role.permissions) if role else []


# -----------------------------------------------------------------------------
# POST /auth/login — Email + Password Login
#
# Flow:
#   1. Check if this IP is rate-limited (too many recent failures)
#   2. Validate that email and password fields are present
#   3. Look up the user by email in the database (must be active)
#   4. Verify the submitted password against the stored bcrypt hash
#   5. On success: issue a JWT token and return user details
#   6. On failure: record the failed attempt, return 401
#
# Special case: If the user exists but has no password (Entra ID user),
# show a helpful message directing them to use the Microsoft button.
# -----------------------------------------------------------------------------
@router.post("/login")
def login(payload: dict, request: Request, db: Session = Depends(get_db)):
    from models.user import User
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip)

    email    = (payload.get("email") or payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required")

    # Look up user — must exist and be active (is_active = True)
    user = db.query(User).filter(User.email == email, User.is_active == True).first()
    if not user or not user.password_hash or not _verify(password, user.password_hash):
        _record_failure(ip)
        # Friendly message for Entra ID users who try to use password login
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


# -----------------------------------------------------------------------------
# POST /auth/verify — Validate a Saved Session Token
#
# Called by the browser on every page load to check if the saved token
# is still valid (not expired, not tampered, user still active).
# If valid: returns user details so the app can restore the session.
# If invalid: returns 401 so the browser redirects to the login page.
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# GET /auth/me — Get Current User Profile
# Returns the logged-in user's own details.
# Requires a valid JWT token (protected by get_current_user from deps.py).
# -----------------------------------------------------------------------------
@router.get("/me")
def me(current_user=Depends(get_current_user)):
    return {
        "id":    current_user.id,
        "name":  current_user.name,
        "email": current_user.email,
        "role":  current_user.role,
    }


# -----------------------------------------------------------------------------
# GET /auth/entra-config — Microsoft Login Configuration
#
# Called by the browser on the login page to decide whether to show
# the "Sign in with Microsoft" button.
# Returns the Azure App Registration client_id and tenant_id.
# If both are set in .env → button is shown.
# If either is empty → button is hidden (password login only).
# These values are NOT secret — they are safe to expose to the browser.
# -----------------------------------------------------------------------------
@router.get("/entra-config")
def entra_config():
    return {
        "client_id": os.getenv("ENTRA_CLIENT_ID", ""),
        "tenant_id": os.getenv("ENTRA_TENANT_ID", ""),
    }


# -----------------------------------------------------------------------------
# POST /auth/microsoft — Microsoft Entra ID Login
#
# Flow:
#   1. Browser opens Microsoft popup (via MSAL.js)
#   2. User signs in with their Kforce Microsoft account
#   3. Microsoft returns an ID token to the browser
#   4. Browser sends that ID token to this endpoint
#   5. This endpoint validates the token with Microsoft's public keys (JWKS)
#      — no secret shared with Microsoft, uses public cryptography
#   6. Extract the user's email from the validated token
#   7. Look up that email in the K Recruit users table
#   8. If found and active → issue a K Recruit JWT token (same as password login)
#   9. If not found → 403 "Contact your admin" (admin must add the user first)
#
# Data sent OUTSIDE the application: only a request to Microsoft's public
# key endpoint (login.microsoftonline.com) to fetch signing keys.
# No resume data, no candidate data leaves the server.
# -----------------------------------------------------------------------------
@router.post("/microsoft")
def microsoft_login(payload: dict, db: Session = Depends(get_db)):
    from models.user import User
    import requests as _req

    id_token = payload.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=400, detail="id_token is required")

    # These must be set in .env — provided by IT from Azure App Registration
    tenant_id = os.getenv("ENTRA_TENANT_ID", "")
    client_id = os.getenv("ENTRA_CLIENT_ID", "")
    if not tenant_id or not client_id:
        raise HTTPException(status_code=503, detail="Microsoft login is not configured on this server.")

    # Fetch Microsoft's public signing keys to validate the token.
    # This is a public endpoint — no credentials needed.
    # These keys are rotated by Microsoft periodically.
    try:
        from jose import jwt as _jwt, jwk, JWTError
        jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        jwks_resp = _req.get(jwks_url, timeout=10)
        jwks_resp.raise_for_status()
        jwks_data = jwks_resp.json()

        # Find the specific key that was used to sign this token (matched by key ID)
        unverified = _jwt.get_unverified_header(id_token)
        kid = unverified.get("kid")
        key = next((k for k in jwks_data["keys"] if k.get("kid") == kid), None)
        if not key:
            raise HTTPException(status_code=401, detail="Microsoft token key not found.")

        # Validate the token: checks signature, expiry, and that it was issued for our app
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

    # Extract the user's email from the validated token claims
    email = (
        claims.get("preferred_username") or
        claims.get("email") or
        claims.get("upn") or ""
    ).strip().lower()
    name = claims.get("name", email.split("@")[0].title())

    if not email:
        raise HTTPException(status_code=401, detail="Microsoft token did not contain an email address.")

    # Look up the user in K Recruit by their Kforce email address.
    # The user must have been added to K Recruit by an admin first.
    # Microsoft only proves identity — K Recruit controls who has access and what role.
    user = db.query(User).filter(
        User.email.ilike(email),
        User.is_active == True
    ).first()

    if not user:
        raise HTTPException(
            status_code=403,
            detail="Your account is not set up in K Recruit. Contact your admin."
        )

    # Issue a K Recruit JWT — identical to what password login returns.
    # From this point, the browser uses this token for all API calls.
    token = _make_token(user.id, user.role)
    return {
        "success":     True,
        "token":       token,
        "username":    user.name or name,
        "role":        user.role,
        "user_id":     user.id,
        "permissions": _get_permissions(db, user.role),
    }
