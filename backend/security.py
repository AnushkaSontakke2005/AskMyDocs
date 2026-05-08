import hashlib
import hmac
import base64
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db import Users, initialize_database


TOKEN_SECRET_KEY = os.getenv("TOKEN_SECRET_KEY", "dev-only-change-this-secret")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))

bearer_scheme = HTTPBearer()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"{salt.hex()}:{password_hash.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, expected_hash = stored_hash.split(":", 1)
    except ValueError:
        return False
    candidate = hash_password(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
    return hmac.compare_digest(candidate, expected_hash)


def create_access_token(user: Users) -> str:
    expires_at = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "exp": int(expires_at.timestamp()),
    }
    encoded_payload = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}"


def authenticate_user(email: str, password: str) -> Optional[Users]:
    initialize_database()
    user = Users.get_or_none(Users.email == normalize_email(email))
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def get_current_api_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> Users:
    initialize_database()
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = decode_access_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_error
        if int(payload.get("exp", 0)) < int(datetime.utcnow().timestamp()):
            raise credentials_error
    except (ValueError, TypeError):
        raise credentials_error

    user = Users.get_or_none(Users.id == int(user_id))
    if not user:
        raise credentials_error
    return user


def decode_access_token(token: str) -> dict:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        raise ValueError("Invalid token format.")

    expected_signature = _sign(encoded_payload)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid token signature.")

    payload_bytes = _urlsafe_b64decode(encoded_payload)
    return json.loads(payload_bytes.decode("utf-8"))


def _sign(encoded_payload: str) -> str:
    digest = hmac.new(
        TOKEN_SECRET_KEY.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return _urlsafe_b64encode(digest)


def _urlsafe_b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
