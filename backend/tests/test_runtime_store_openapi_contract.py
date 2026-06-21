from fastapi import FastAPI

from app.gateway.routers import mcp, skills


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp.router)
    app.include_router(skills.router)
    return app


def _operation_description(app: FastAPI, path: str, method: str) -> str:
    return app.openapi()["paths"][path][method]["description"]


def test_runtime_mutation_openapi_descriptions_are_storage_source_neutral():
    app = _make_app()

    descriptions = [
        _operation_description(app, "/api/mcp/config", "put"),
        _operation_description(app, "/api/skills/{skill_name}", "put"),
    ]

    for description in descriptions:
        lowered = description.lower()
        assert "active extensions configuration source" in lowered
        assert "save to file" not in lowered
        assert "mcp_config.json" not in lowered
        assert "extensions_config.json file" not in lowered
