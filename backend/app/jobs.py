import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from .delivery import build_delivery_url
from .extraction import ExtractionError, ExtractionUnavailable, MediaResolver
from .models import (
    DownloadItem,
    DownloadItemStatus,
    DownloadJob,
    ErrorDetail,
    JobStatus,
    MediaAsset,
    MediaDetails,
)
from .settings import Settings
from .state import StateBackend
from .url_validation import ValidatedUrl


@dataclass(frozen=True)
class SubmittedUrl:
    raw_url: str
    parsed: ValidatedUrl | None
    error: ErrorDetail | None


class DownloadService:
    def __init__(
        self,
        resolver: MediaResolver,
        state: StateBackend,
        settings: Settings,
    ):
        self._resolver = resolver
        self._state = state
        self._settings = settings

    async def submit(self, urls: list[SubmittedUrl]) -> DownloadJob:
        now = datetime.now(timezone.utc)
        job = DownloadJob(
            id=secrets.token_urlsafe(18),
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            items=[],
        )
        parsed_urls: list[ValidatedUrl | None] = []

        for submitted in urls:
            if submitted.error is not None:
                job.items.append(
                    DownloadItem(
                        url=submitted.raw_url,
                        status=DownloadItemStatus.FAILED,
                        error=submitted.error,
                    )
                )
                parsed_urls.append(None)
                continue

            if submitted.parsed is None or not submitted.parsed.supported:
                message = submitted.parsed.message if submitted.parsed is not None else "Unsupported URL."
                job.items.append(
                    DownloadItem(
                        url=submitted.raw_url,
                        status=DownloadItemStatus.FAILED,
                        error=ErrorDetail(code="unsupported_url", message=message),
                    )
                )
                parsed_urls.append(submitted.parsed)
                continue

            job.items.append(DownloadItem(url=submitted.raw_url))
            parsed_urls.append(submitted.parsed)

        await self._process(job, parsed_urls)
        for index, item in enumerate(job.items):
            if item.status == DownloadItemStatus.COMPLETED and item.media is not None:
                item.download_url = f"/api/downloads/{job.id}/items/{index}/download"
        await self._state.job_save(
            job.id,
            job.model_dump_json(),
            self._settings.job_ttl_seconds,
        )
        return job.model_copy(deep=True)

    async def get(self, job_id: str) -> DownloadJob | None:
        raw = await self._state.job_get(job_id)
        if raw is None:
            return None
        try:
            return DownloadJob.model_validate_json(raw)
        except ValidationError:
            return None

    async def get_cached_media(self, provider_id: str, media_id: str) -> MediaAsset | None:
        raw = await self._state.cache_get(f"{provider_id}:{media_id}")
        if raw is None:
            return None
        try:
            return MediaAsset.model_validate_json(raw)
        except ValidationError:
            return None

    async def get_cached_details(
        self,
        provider_id: str,
        media_id: str,
    ) -> MediaDetails | None:
        raw = await self._state.cache_get(f"details:{provider_id}:{media_id}")
        if raw is None:
            return None
        try:
            return MediaDetails.model_validate_json(raw)
        except ValidationError:
            return None

    async def inspect(self, url: ValidatedUrl) -> tuple[MediaDetails, bool]:
        if url.provider is None or url.content_id is None:
            raise ExtractionError("unsupported_url", "Unsupported URL.")

        details = await self.get_cached_details(url.provider.id, url.content_id)
        if details is not None:
            return details, True

        details = await self._resolver.inspect(url)
        if details.provider != url.provider.id or details.id != url.content_id:
            raise ExtractionError(
                "invalid_media_result",
                "The media resolver returned unexpected details.",
            )

        await self._state.cache_set(
            f"details:{details.provider}:{details.id}",
            details.model_dump_json(),
            self._settings.cache_ttl_seconds,
        )
        return details, False

    async def _process(self, job: DownloadJob, parsed_urls: list[ValidatedUrl | None]) -> None:
        job.status = JobStatus.PROCESSING
        job.updated_at = datetime.now(timezone.utc)

        for index, item in enumerate(job.items):
            if item.status != DownloadItemStatus.QUEUED:
                continue

            parsed = parsed_urls[index]
            if parsed is None or parsed.provider is None or parsed.content_id is None:
                item.status = DownloadItemStatus.FAILED
                item.error = ErrorDetail(code="unsupported_url", message="Unsupported URL.")
                continue

            cache_key = f"{parsed.provider.id}:{parsed.content_id}"
            cached = await self._cached_asset(cache_key)
            if cached is not None:
                self._complete_item(item, cached, cache_hit=True)
                continue

            try:
                asset = await self._resolver.resolve(parsed)
            except ExtractionUnavailable as exc:
                item.status = DownloadItemStatus.FAILED
                item.error = ErrorDetail(code=exc.code, message=exc.message)
                continue
            except Exception:
                item.status = DownloadItemStatus.FAILED
                item.error = ErrorDetail(
                    code="extraction_failed",
                    message="The media could not be extracted.",
                )
                continue

            if asset.provider != parsed.provider.id or asset.id != parsed.content_id:
                item.status = DownloadItemStatus.FAILED
                item.error = ErrorDetail(
                    code="invalid_media_result",
                    message="The media resolver returned an unexpected asset.",
                )
                continue

            await self._state.cache_set(
                cache_key,
                asset.model_dump_json(),
                self._settings.cache_ttl_seconds,
            )
            self._complete_item(item, asset, cache_hit=False)

        statuses = [item.status for item in job.items]
        if statuses and all(status == DownloadItemStatus.COMPLETED for status in statuses):
            job.status = JobStatus.COMPLETED
        elif statuses and all(status == DownloadItemStatus.FAILED for status in statuses):
            job.status = JobStatus.FAILED
        elif any(status == DownloadItemStatus.COMPLETED for status in statuses):
            job.status = JobStatus.PARTIAL
        else:
            job.status = JobStatus.FAILED
        job.updated_at = datetime.now(timezone.utc)

    async def _cached_asset(self, cache_key: str) -> MediaAsset | None:
        raw = await self._state.cache_get(cache_key)
        if raw is None:
            return None
        try:
            return MediaAsset.model_validate_json(raw)
        except ValidationError:
            return None

    def _complete_item(self, item: DownloadItem, asset: MediaAsset, cache_hit: bool) -> None:
        item.status = DownloadItemStatus.COMPLETED
        item.media = asset
        item.delivery_url = build_delivery_url(asset, self._settings.cdn_base_url)
        item.cache_hit = cache_hit
        item.error = None
