from __future__ import annotations

from fastapi import Header, HTTPException

from atlas.settings import Settings


ADMIN_TOKEN_HEADER = "X-Atlas-Admin-Token"


def require_admin_token(
    x_atlas_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> None:
    settings = Settings()

    configured = (settings.atlas_admin_token or "").strip()

    # Dev convenience: if no token is configured, keep admin endpoints open.
    if settings.atlas_env == "dev" and not configured:
        return

    if not configured:
        raise HTTPException(status_code=401, detail="Admin token not configured")

    if not x_atlas_admin_token or x_atlas_admin_token != configured:
        raise HTTPException(status_code=403, detail="Invalid admin token")
