"""Gateway router for runtime config metadata."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.gateway.deps import require_admin_user
from deerflow.config.reload_boundary import STARTUP_ONLY_FIELDS, STARTUP_ONLY_PREFIX

router = APIRouter(prefix="/api/config", tags=["config"])

_ADMIN_REQUIRED_DETAIL = "Admin privileges required to inspect runtime configuration metadata."


class ReloadBoundaryFieldResponse(BaseModel):
    requires_restart: bool = Field(description="Whether this top-level AppConfig field is captured at startup")
    reason: str = Field(description="Human-readable reason the field requires a process restart")


class ReloadBoundaryResponse(BaseModel):
    startup_only_prefix: str = Field(description="Prefix used on startup-only AppConfig field descriptions")
    fields: dict[str, ReloadBoundaryFieldResponse] = Field(description="Startup-only top-level AppConfig fields keyed by field name")


@router.get(
    "/reload-boundary",
    response_model=ReloadBoundaryResponse,
    summary="Get Config Reload Boundary",
    description="Return top-level config fields whose changes require a process restart.",
)
async def get_reload_boundary(request: Request) -> ReloadBoundaryResponse:
    """Expose the restart-required AppConfig boundary to admin tooling."""
    await require_admin_user(request, detail=_ADMIN_REQUIRED_DETAIL)
    return ReloadBoundaryResponse(
        startup_only_prefix=STARTUP_ONLY_PREFIX,
        fields={
            name: ReloadBoundaryFieldResponse(requires_restart=True, reason=reason)
            for name, reason in sorted(STARTUP_ONLY_FIELDS.items())
        },
    )
