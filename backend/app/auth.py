import secrets
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException

from .settings import Settings, get_settings


def require_role(role: str) -> Callable[..., None]:
    """Declare the role a route needs. M1: one shared token; real users/roles in M5."""

    def check(
        authorization: str = Header(default=""), settings: Settings = Depends(get_settings)
    ) -> None:
        token = settings.qarau_api_token
        if not token:  # dev mode
            return
        supplied = authorization.removeprefix("Bearer ")
        if not secrets.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "invalid or missing token", {"WWW-Authenticate": "Bearer"})

    return check
