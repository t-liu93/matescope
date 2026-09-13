from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    docs_url="/api/docs",
    redoc_url=None,
)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    service: str


@app.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="matescope", version=settings.version)


@app.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["operations"])
def readiness() -> ReadinessResponse:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    return ReadinessResponse(status="ready", service="matescope")


def create_app() -> FastAPI:
    static_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False, response_model=None)
        async def spa(path: str) -> FileResponse | JSONResponse:
            if path == "api" or path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app


app = create_app()
