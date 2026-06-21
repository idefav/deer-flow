from __future__ import annotations

from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import config


def _admin_user() -> User:
    return User(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


def _non_admin_user() -> User:
    return User(
        id=UUID("99999999-8888-7777-6666-555555555555"),
        email="user@example.com",
        password_hash="x",
        system_role="user",
    )


def test_reload_boundary_requires_admin() -> None:
    app = make_authed_test_app(user_factory=_non_admin_user)
    app.include_router(config.router)

    with TestClient(app) as client:
        response = client.get("/api/config/reload-boundary")

    assert response.status_code == 403
    assert "Admin privileges" in response.json()["detail"]


def test_reload_boundary_returns_startup_only_fields() -> None:
    app = make_authed_test_app(user_factory=_admin_user)
    app.include_router(config.router)

    with TestClient(app) as client:
        response = client.get("/api/config/reload-boundary")

    assert response.status_code == 200
    body = response.json()
    assert body["startup_only_prefix"] == "startup-only:"
    assert body["fields"]["database"]["requires_restart"] is True
    assert "SQLAlchemy engine" in body["fields"]["database"]["reason"]
    assert body["fields"]["sandbox"]["requires_restart"] is True
