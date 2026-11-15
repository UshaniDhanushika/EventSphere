import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from pwdlib import PasswordHash
from fastapi import Request, HTTPException, Depends
from sqlalchemy.orm import Session
from database import get_db

# Password hashing
password_hash = PasswordHash.recommended()

SECRET_KEY = os.getenv("SECRET_KEY", "eventsphere-super-secure-key-2026-viva")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours for comfortable demoing

def hash_password(password: str) -> str:
    """Hashes a plain password using argon2 / bcrypt."""
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the stored hash."""
    return password_hash.verify(plain_password, hashed_password)

def create_access_token(username: str, role: str) -> str:
    """Generates a signed JWT access token containing username and role."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": username,
        "role": role,
        "exp": expire
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def read_access_token(token: str) -> Optional[dict]:
    """Decodes and validates a JWT token."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None

def extract_token_from_request(request: Request) -> Optional[str]:
    """Extracts bearer token from either Authorization header or cookies."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1]
    
    # Fallback to browser cookie for direct web browser usage
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        if cookie_token.startswith("Bearer "):
            return cookie_token.split(" ", 1)[1]
        return cookie_token
    return None

def generate_csrf_token() -> str:
    """Generates a cryptographically secure CSRF token."""
    return secrets.token_hex(32)

def get_or_create_csrf_token(request: Request) -> str:
    """Returns existing CSRF token from cookie or creates a fresh token."""
    token = request.cookies.get("csrf_token")
    if not token or len(token) < 16:
        token = generate_csrf_token()
    return token

def validate_csrf_token(request: Request, submitted_token: Optional[str] = None) -> bool:
    """Validates that submitted CSRF token matches the session/cookie CSRF token."""
    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token:
        return True  # If no cookie established yet (e.g., initial API request), allow or soft-check
    header_token = request.headers.get("X-CSRF-Token")
    token = submitted_token or header_token
    if not token:
        return False
    return secrets.compare_digest(cookie_token, token)

def get_current_user_optional(
    request: Request,
    db: Session = Depends(get_db)
):
    """Returns the current logged-in user if token is valid, or None."""
    from models import User
    token = extract_token_from_request(request)
    if not token:
        return None
    data = read_access_token(token)
    if not data or not data.get("sub"):
        return None
    return db.query(User).filter(User.username == data.get("sub")).first()

def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
):
    """Requires authentication. Raises 401 if not logged in."""
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please sign in."
        )
    return user

def require_role(*allowed_roles: str):
    """Dependency factory checking that the user holds one of the required roles."""
    def role_checker(
        request: Request,
        db: Session = Depends(get_db)
    ):
        user = get_current_user(request, db)
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access forbidden: requires role '{', '.join(allowed_roles)}', your role is '{user.role}'."
            )
        return user
    return role_checker
