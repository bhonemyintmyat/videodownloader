from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Provider(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str


class ValidationResponse(BaseModel):
    valid: bool
    supported: bool
    message: str
    original_url: str
    normalized_url: str | None = None
    provider: Provider | None = None
    content_id: str | None = None
    content_type: str | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class MediaAsset(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    provider: str
    title: str | None = None
    source_url: str
    media_url: str
    content_type: str = "video/mp4"
    size_bytes: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    format_id: str | None = None


class MediaFormat(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    label: str
    media_url: str
    content_type: str = "video/mp4"
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    size_bytes: int | None = Field(default=None, ge=0)


class MediaDetails(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    provider: str
    title: str | None = None
    source_url: str
    thumbnail_url: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    formats: list[MediaFormat] = Field(default_factory=list)


class MediaFormatOption(BaseModel):
    id: str
    label: str
    content_type: str
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None


class MediaDetailsResponse(BaseModel):
    id: str
    provider: str
    provider_name: str
    title: str | None = None
    source_url: str
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    formats: list[MediaFormatOption]
    cache_hit: bool = False


class DownloadItemStatus(str, Enum):
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DownloadItem(BaseModel):
    url: str
    status: DownloadItemStatus = DownloadItemStatus.QUEUED
    media: MediaAsset | None = None
    delivery_url: str | None = None
    download_url: str | None = None
    cache_hit: bool = False
    error: ErrorDetail | None = None


class DownloadJob(BaseModel):
    id: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime
    updated_at: datetime
    items: list[DownloadItem]


class UrlValidationRequest(BaseModel):
    url: str = Field(min_length=1)


class DownloadRequest(BaseModel):
    urls: list[str] = Field(min_length=1)
