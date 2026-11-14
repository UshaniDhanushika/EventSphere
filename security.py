
import os
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash


# Password hashing tool
password_hash = PasswordHash.recommended()


# Secret key for signing tokens
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "development-only-change-this-secret"
)

ALGORITHM = "HS256"


# Convert password into a secure hash
def hash_password(password):
    return password_hash.hash(password)


# Check whether the password is correct
def verify_password(password, hashed_password):
    return password_hash.verify(password, hashed_password)


# Create a login token
def create_access_token(username, role):

    expire_time = datetime.now(timezone.utc) + timedelta(minutes=30)

    token_data = {
        "sub": username,
        "role": role,
        "exp": expire_time
    }

    token = jwt.encode(
        token_data,
        SECRET_KEY,
        algorithm=ALGORITHM
    )

    return token


# Read and verify a login token
def read_access_token(token):

    return jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM]
    )
