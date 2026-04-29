"""HTTP Basic Auth til dashboard og /api/*-endpoints.

Loginoplysninger via env-vars BASIC_AUTH_USER og BASIC_AUTH_PASSWORD.
Hvis ikke sat (lokal dev), skipper auth.
"""

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)


def require_basic_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    expected_user = os.environ.get("BASIC_AUTH_USER")
    expected_password = os.environ.get("BASIC_AUTH_PASSWORD")

    # Lokal dev / ikke konfigureret: skip auth
    if not expected_user or not expected_password:
        return "anonymous"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login kraevet",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username.encode(), expected_user.encode())
    pass_ok = secrets.compare_digest(credentials.password.encode(), expected_password.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Forkert brugernavn eller password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
