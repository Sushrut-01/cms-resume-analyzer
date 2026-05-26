# =============================================================================
# deps.py — Authentication & Authorization Guards
#
# This file is the security gatekeeper for the entire application.
# Every protected API endpoint runs these checks BEFORE executing.
# No endpoint can be accessed without passing through here first.
# =============================================================================

import os
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from database import get_db

# Read the JWT secret key from the .env file.
# This key is used to sign and verify all login tokens.
# If it is missing, the server refuses to start entirely — fail safe.
SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET environment variable is not set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

# HS256 = HMAC with SHA-256. The algorithm used to sign the JWT token.
ALGORITHM  = "HS256"

# Reads the "Authorization: Bearer <token>" header from every incoming request.
# auto_error=False means we handle the missing token ourselves (custom error message).
_bearer = HTTPBearer(auto_error=False)


# -----------------------------------------------------------------------------
# get_current_user
# Runs before any endpoint that requires a logged-in user.
# Steps:
#   1. Extract the JWT token from the request header
#   2. Verify the token signature using JWT_SECRET
#   3. Check the token has not expired (tokens expire after 24 hours)
#   4. Look up the user in the database to confirm they are still active
#   5. Return the user object so the endpoint can use it (e.g. check role)
# If any step fails → 401 Unauthorized is returned, request is blocked.
# -----------------------------------------------------------------------------
def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db:    Session                       = Depends(get_db),
):
    from jose import jwt, JWTError
    from models.user import User

    # Step 1: No token in the request header at all → reject immediately
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        # Step 2 & 3: Decode the token — this automatically checks:
        #   - Is the signature valid? (was it signed by our JWT_SECRET?)
        #   - Has the token expired? (exp field inside the token)
        # If either check fails, JWTError is raised and caught below.
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])

        # Step 4: Extract the user ID that was embedded in the token at login time
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        # Token was tampered with, or it has expired → force re-login
        raise HTTPException(status_code=401, detail="Session expired")

    # Step 5: Confirm the user still exists and is active in the database.
    # This catches cases where an admin deactivated the user after they logged in.
    user = db.query(User).filter(User.id == int(user_id), User.is_active == True).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Return the full user object — endpoints use this to check name, role, etc.
    return user


# -----------------------------------------------------------------------------
# require_admin
# Stacked on top of get_current_user.
# First checks: valid token + active user (via get_current_user)
# Then checks: user must have role "admin" or "super_admin"
# Used on: User management, Job Description management, AI Settings endpoints
# If role is "recruiter" → 403 Forbidden is returned.
# -----------------------------------------------------------------------------
def require_admin(user=Depends(get_current_user)):
    if user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# -----------------------------------------------------------------------------
# require_super_admin
# Strictest access level — only "super_admin" role passes.
# Used on: Role management (create/edit/delete roles)
# Even admins are blocked here.
# -----------------------------------------------------------------------------
def require_super_admin(user=Depends(get_current_user)):
    if user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return user
