from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.notifications import router as notification_router
from app.api.routes import router
from app.api.workspace import router as workspace_router
from app.config import get_settings

settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Operator-Token", "X-Internal-Token"],
)
app.include_router(router)
app.include_router(notification_router)
app.include_router(workspace_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    connect_sources = ["'self'"]
    if settings.s3_upload_bucket:
        connect_sources.append(
            f"https://{settings.s3_upload_bucket}.s3.{settings.aws_region}.amazonaws.com"
        )
    content_security_policy = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        f"font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src {' '.join(connect_sources)}; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; upgrade-insecure-requests"
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", content_security_policy)
    if settings.app_env.lower() == "production":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    if request.url.path in {"/", "/index.html", "/overview", "/applicants", "/policies"}:
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
web_index = web_dist / "index.html"
if web_index.is_file():

    @app.get("/overview", include_in_schema=False)
    @app.get("/applicants", include_in_schema=False)
    @app.get("/policies", include_in_schema=False)
    @app.get("/", include_in_schema=False)
    def console_index() -> FileResponse:
        return FileResponse(web_index, media_type="text/html")

    app.mount("/", StaticFiles(directory=str(web_dist)), name="console")
