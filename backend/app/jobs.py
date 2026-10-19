import asyncio
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .cache import TTLCache
from .delivery import build_delivery_url
from .extraction import ExtractionUnavailable, MediaResolver
from .models import (
    DownloadItem,
    DownloadItemStatus,
    DownloadJob,
    ErrorDetail,
    JobStatus,
    MediaAsset,
)
from .settings import Settings
from .url_validation import ValidatedUrl


class QueueFullError(Exception):
    pass


class NoSupportedUrlsError(Exception):
    pass


@dataclass(frozen=True)
class SubmittedUrl:
    raw_url: str
    parsed: ValidatedUrl | None
    error: ErrorDetail | None


@dataclass
class _JobRecord:
    job: DownloadJob
    parsed_urls: list[ValidatedUrl | None] = field(default_factory=list)


class DownloadQueue:
    def __init__(
        self,
        resolver: MediaResolver,
        cache: TTLCache[str, MediaAsset],
        settings: Settings,
    ):
        self._resolver = resolver
        self._cache = cache
        self._settings = settings
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=settings.queue_max_size)
        self._records: OrderedDict[str, _JobRecord] = OrderedDict()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        for index in range(self._settings.queue_workers):
            self._tasks.append(asyncio.create_task(self._worker(), name=f"download-worker-{index}"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def submit(self, urls: list[SubmittedUrl]) -> DownloadJob:
        self._prune_jobs()
        supported = [item for item in urls if item.parsed is not None and item.parsed.supported]
        if not supported:
            raise NoSupportedUrlsError()

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

        record = _JobRecord(job=job, parsed_urls=parsed_urls)
        self._records[job.id] = record

        try:
            self._queue.put_nowait(job.id)
        except asyncio.QueueFull:
            del self._records[job.id]
            raise QueueFullError() from None

        self._prune_jobs()
        return job.model_copy(deep=True)

    def get(self, job_id: str) -> DownloadJob | None:
        self._prune_jobs()
        record = self._records.get(job_id)
        if record is None:
            return None
        return record.job.model_copy(deep=True)

    def get_cached_media(self, provider_id: str, media_id: str) -> MediaAsset | None:
        return self._cache.get(f"{provider_id}:{media_id}")

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                record = self._records.get(job_id)
                if record is not None:
                    await self._process(record)
            finally:
                self._queue.task_done()

    async def _process(self, record: _JobRecord) -> None:
        record.job.status = JobStatus.PROCESSING
        record.job.updated_at = datetime.now(timezone.utc)

        for index, item in enumerate(record.job.items):
            if item.status != DownloadItemStatus.QUEUED:
                continue

            parsed = record.parsed_urls[index]
            if parsed is None or parsed.provider is None or parsed.content_id is None:
                item.status = DownloadItemStatus.FAILED
                item.error = ErrorDetail(code="unsupported_url", message="Unsupported URL.")
                continue

            cache_key = f"{parsed.provider.id}:{parsed.content_id}"
            cached = self._cache.get(cache_key)
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

            self._cache.set(cache_key, asset)
            self._complete_item(item, asset, cache_hit=False)

        statuses = [item.status for item in record.job.items]
        if statuses and all(status == DownloadItemStatus.COMPLETED for status in statuses):
            record.job.status = JobStatus.COMPLETED
        elif statuses and all(status == DownloadItemStatus.FAILED for status in statuses):
            record.job.status = JobStatus.FAILED
        elif any(status == DownloadItemStatus.COMPLETED for status in statuses):
            record.job.status = JobStatus.PARTIAL
        else:
            record.job.status = JobStatus.FAILED
        record.job.updated_at = datetime.now(timezone.utc)

    def _complete_item(self, item: DownloadItem, asset: MediaAsset, cache_hit: bool) -> None:
        item.status = DownloadItemStatus.COMPLETED
        item.media = asset
        item.delivery_url = build_delivery_url(asset, self._settings.cdn_base_url)
        item.cache_hit = cache_hit
        item.error = None

    def _prune_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        ttl = timedelta(seconds=self._settings.job_ttl_seconds)
        terminal = {JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED}

        for job_id, record in list(self._records.items()):
            if record.job.status in terminal and now - record.job.updated_at > ttl:
                del self._records[job_id]

        while len(self._records) > self._settings.queue_max_jobs:
            removable = next(
                (
                    job_id
                    for job_id, record in self._records.items()
                    if record.job.status in terminal
                ),
                None,
            )
            if removable is None:
                break
            del self._records[removable]
