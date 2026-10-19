import re
from contextlib import asynccontextmanager
from ipaddress import ip_address

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from .delivery import build_delivery_url
from .extraction import MediaResolver, UnavailableResolver
from .jobs import DownloadService, SubmittedUrl
from .models import (
    DownloadJob,
    DownloadRequest,
    ErrorDetail,
    Provider,
    UrlValidationRequest,
    ValidationResponse,
)
from .providers import PROVIDERS, get_provider
from .settings import Settings
from .state import StateBackendError, create_state
from .url_validation import UrlValidationError, validate_video_url


_MEDIA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _validation_response(original_url: str, validated) -> ValidationResponse:
    return ValidationResponse(
        valid=True,
        supported=validated.supported,
        message=validated.message,
        original_url=original_url,
        normalized_url=validated.normalized_url,
        provider=validated.provider,
        content_id=validated.content_id,
        content_type=validated.content_type,
    )


def _client_key(request: Request, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            candidate = forwarded.split(",", 1)[0].strip()
            try:
                ip_address(candidate)
            except ValueError:
                pass
            else:
                return candidate
    if request.client is not None:
        return request.client.host
    return "unknown"


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_app(
    settings: Settings | None = None,
    resolver: MediaResolver | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    resolver = resolver or UnavailableResolver()
    state = create_state(settings)
    download_service = DownloadService(resolver=resolver, state=state, settings=settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await state.close()

    app = FastAPI(
        title="VideoSaver API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.state = state
    app.state.download_service = download_service

    @app.exception_handler(StateBackendError)
    async def state_backend_error_handler(request: Request, exc: StateBackendError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": {
                    "code": "state_unavailable",
                    "message": "The download service is temporarily unavailable.",
                }
            },
        )

    @app.middleware("http")
    async def enforce_rate_limit(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api") or path == "/api/health":
            return await call_next(request)

        decision = await state.rate_limit(_client_key(request, settings.trust_proxy_headers))
        limit_headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
        }
        if not decision.allowed:
            limit_headers["Retry-After"] = str(decision.retry_after)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": {
                        "code": "rate_limited",
                        "message": "Too many requests. Try again later.",
                    }
                },
                headers=limit_headers,
            )

        response = await call_next(request)
        for header, value in limit_headers.items():
            response.headers[header] = value
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/providers", response_model=list[Provider])
    def providers() -> list[Provider]:
        return [Provider(id=provider.id, name=provider.name) for provider in PROVIDERS]

    @app.post("/api/validate", response_model=ValidationResponse)
    def validate(payload: UrlValidationRequest) -> ValidationResponse:
        try:
            validated = validate_video_url(payload.url)
        except UrlValidationError as exc:
            raise _http_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc
        return _validation_response(payload.url, validated)

    @app.post(
        "/api/downloads",
        response_model=DownloadJob,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_download(job: DownloadRequest) -> DownloadJob:
        if len(job.urls) > settings.job_max_urls:
            raise _http_error(
                status.HTTP_400_BAD_REQUEST,
                "too_many_urls",
                f"Submit at most {settings.job_max_urls} URLs per job.",
            )

        submitted: list[SubmittedUrl] = []
        for raw_url in job.urls:
            try:
                validated = validate_video_url(raw_url)
            except UrlValidationError as exc:
                submitted.append(
                    SubmittedUrl(
                        raw_url=raw_url,
                        parsed=None,
                        error=ErrorDetail(code=exc.code, message=exc.message),
                    )
                )
                continue

            submitted.append(SubmittedUrl(raw_url=raw_url, parsed=validated, error=None))

        if not any(item.parsed is not None and item.parsed.supported for item in submitted):
            raise _http_error(
                status.HTTP_400_BAD_REQUEST,
                "no_supported_urls",
                "No supported X video URLs were provided.",
            )

        return await download_service.submit(submitted)

    @app.get("/api/downloads/{job_id}", response_model=DownloadJob)
    async def get_download(job_id: str) -> DownloadJob:
        job = await download_service.get(job_id)
        if job is None:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "job_not_found",
                "The download job was not found.",
            )
        return job

    @app.get("/api/media/{provider_id}/{media_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    async def deliver_media(provider_id: str, media_id: str) -> RedirectResponse:
        if get_provider(provider_id) is None or _MEDIA_ID_PATTERN.fullmatch(media_id) is None:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "media_not_found",
                "The media file was not found.",
            )

        asset = await download_service.get_cached_media(provider_id, media_id)
        if asset is None:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "media_not_found",
                "The media file is not cached or has expired.",
            )

        return RedirectResponse(
            build_delivery_url(asset, settings.cdn_base_url),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Cache-Control": "private, max-age=60"},
        )

    return app


app = create_app()
