import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from .auth import LoginLimiter, password_hasher, router
from .config import Settings, settings
from .storage import Storage


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    service: str


def create_app(configuration: Settings | None = None) -> FastAPI:
    configuration = configuration or settings

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.storage = Storage(configuration.data_dir)
        application.state.storage.start()
        # Pre-auth CSRF tokens expire on process restart; authenticated sessions persist.
        application.state.csrf_key = secrets.token_bytes(32)
        application.state.login_limiter = LoginLimiter()
        application.state.dummy_password = password_hasher.hash(secrets.token_urlsafe(32))
        try:
            yield
        finally:
            application.state.storage.stop()

    application = FastAPI(
        title=configuration.app_name,
        version=configuration.version,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.include_router(router)

    @application.middleware("http")
    async def sensitive_cache_policy(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors can contain original password input; never echo request bodies.
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields"})

    @application.get("/api/v1/health", response_model=HealthResponse, tags=["operations"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="matescope", version=configuration.version)

    @application.get("/api/v1/readiness", response_model=ReadinessResponse, tags=["operations"])
    def readiness() -> ReadinessResponse:
        return ReadinessResponse(status="ready", service="matescope")

    static_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @application.get("/{path:path}", include_in_schema=False, response_model=None)
        async def spa(path: str) -> FileResponse | JSONResponse:
            if path == "api" or path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = static_dir / path
            if path and candidate.is_file() and static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return application


app = create_app()
