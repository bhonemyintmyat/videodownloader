import asyncio
from urllib.parse import urlsplit

import yt_dlp

from app.extraction import ExtractionError
from app.models import MediaAsset, MediaDetails, MediaFormat
from app.url_validation import ValidatedUrl


_CONTENT_TYPES = {
    "m3u8": "application/vnd.apple.mpegurl",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "mp4": "video/mp4",
    "webm": "video/webm",
}

_VIDEO_EXTENSIONS = {"m4v", "mov", "mp4", "webm"}


class UnauthenticatedYtdlpResolver:
    def __init__(self, timeout_seconds: float = 20.0, socket_timeout: float = 15.0):
        self._timeout_seconds = timeout_seconds
        self._socket_timeout = socket_timeout

    async def resolve(self, url: ValidatedUrl) -> MediaAsset:
        info = await self._extract(url)
        return self._asset_from_info(url, info)

    async def inspect(self, url: ValidatedUrl) -> MediaDetails:
        info = await self._extract(url)
        return self._details_from_info(url, info)

    async def _extract(self, url: ValidatedUrl) -> dict:
        self._ensure_supported(url)

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._extract_sync, url),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise ExtractionError(
                "extraction_timeout",
                "Media extraction timed out.",
            ) from exc

    def _ensure_supported(self, url: ValidatedUrl) -> None:
        if not url.supported or url.provider is None or url.content_id is None:
            raise ExtractionError("unsupported_url", "Unsupported URL.")
        if url.provider.id != "x":
            raise ExtractionError("unsupported_provider", "Only X URLs are supported.")

    def _extract_sync(self, url: ValidatedUrl) -> dict:
        options = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "noplaylist": True,
            "ignoreconfig": True,
            "cachedir": False,
            "socket_timeout": self._socket_timeout,
            "retries": 2,
            "format": "best[acodec!=none][vcodec!=none]/best",
        }

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url.normalized_url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise ExtractionError(
                "extraction_failed",
                "The media could not be extracted.",
            ) from exc
        except Exception as exc:
            raise ExtractionError(
                "extraction_failed",
                "The media could not be extracted.",
            ) from exc

        if not isinstance(info, dict):
            raise ExtractionError(
                "extraction_failed",
                "The media could not be extracted.",
            )

        if info.get("_type") == "playlist":
            entries = [entry for entry in info.get("entries") or [] if isinstance(entry, dict)]
            if not entries:
                raise ExtractionError(
                    "extraction_failed",
                    "The media could not be extracted.",
                )
            info = entries[0]

        return info

    def _asset_from_info(self, url: ValidatedUrl, info: dict) -> MediaAsset:
        selected = self._select_format(info)
        media_url = selected.get("url")
        if not isinstance(media_url, str) or not self._is_http_url(media_url):
            raise ExtractionError(
                "media_url_missing",
                "No downloadable media URL was found.",
            )

        extension = str(selected.get("ext") or info.get("ext") or "").lower()
        duration = info.get("duration")
        size = selected.get("filesize") or selected.get("filesize_approx")
        format_id = selected.get("format_id")

        return MediaAsset(
            id=url.content_id,
            provider=url.provider.id,
            title=info.get("title") or f"X video {url.content_id}",
            source_url=url.normalized_url,
            media_url=media_url,
            content_type=_CONTENT_TYPES.get(extension, "video/mp4"),
            size_bytes=int(size) if isinstance(size, (int, float)) else None,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            format_id=str(format_id) if format_id is not None else None,
        )

    def _details_from_info(self, url: ValidatedUrl, info: dict) -> MediaDetails:
        duration = info.get("duration")
        return MediaDetails(
            id=url.content_id,
            provider=url.provider.id,
            title=info.get("title") or f"X video {url.content_id}",
            source_url=url.normalized_url,
            thumbnail_url=self._thumbnail_url(info),
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
            formats=self._build_formats(info),
        )

    def _thumbnail_url(self, info: dict) -> str | None:
        candidates = [info.get("thumbnail")]
        candidates.extend(
            item.get("url")
            for item in info.get("thumbnails") or []
            if isinstance(item, dict)
        )
        for value in candidates:
            if isinstance(value, str) and self._is_http_url(value):
                return value
        return None

    def _build_formats(self, info: dict) -> list[MediaFormat]:
        candidates: list[dict] = []

        for item in info.get("formats") or []:
            if not isinstance(item, dict) or not self._is_progressive_video(item):
                continue
            candidate = self._format_candidate(item)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates and self._is_progressive_video(info):
            candidate = self._format_candidate(info)
            if candidate is not None:
                candidates.append(candidate)

        best_by_quality: dict[tuple[int, str], dict] = {}
        for candidate in candidates:
            key = (candidate["height"], candidate["ext"])
            current = best_by_quality.get(key)
            if current is None or self._format_score(candidate) > self._format_score(current):
                best_by_quality[key] = candidate

        ordered = sorted(
            best_by_quality.values(),
            key=lambda item: (
                item["height"],
                item["tbr"],
                item["size_bytes"] or 0,
            ),
            reverse=True,
        )

        formats: list[MediaFormat] = []
        used_ids: set[str] = set()
        for item in ordered[:8]:
            format_id = item["id"]
            if format_id in used_ids:
                format_id = f"{format_id}-{len(formats) + 1}"
            used_ids.add(format_id)

            formats.append(
                MediaFormat(
                    id=format_id,
                    label=self._format_label(item),
                    media_url=item["url"],
                    content_type=_CONTENT_TYPES.get(item["ext"], "video/mp4"),
                    width=item["width"],
                    height=item["height"],
                    size_bytes=item["size_bytes"],
                )
            )

        return formats

    def _is_progressive_video(self, item: dict) -> bool:
        url = item.get("url")
        if not isinstance(url, str) or not self._is_http_url(url):
            return False

        protocol = str(item.get("protocol") or "").lower()
        if "m3u8" in protocol or urlsplit(url).path.lower().endswith(".m3u8"):
            return False

        extension = str(item.get("ext") or "").lower()
        if extension not in _VIDEO_EXTENSIONS:
            return False
        if item.get("vcodec") == "none" or item.get("acodec") == "none":
            return False

        height = item.get("height")
        return isinstance(height, int) and height > 0

    def _format_candidate(self, item: dict) -> dict | None:
        height = item.get("height")
        if not isinstance(height, int) or height <= 0:
            return None

        width = item.get("width")
        size = item.get("filesize") or item.get("filesize_approx")
        tbr = item.get("tbr")
        extension = str(item.get("ext") or "mp4").lower()
        format_id = str(item.get("format_id") or f"{height}p-{extension}")
        url = item.get("url")

        return {
            "id": format_id[:128],
            "url": url,
            "ext": extension,
            "height": height,
            "width": int(width) if isinstance(width, int) and width > 0 else None,
            "size_bytes": int(size) if isinstance(size, (int, float)) else None,
            "tbr": float(tbr) if isinstance(tbr, (int, float)) else 0.0,
        }

    def _format_score(self, item: dict) -> tuple:
        return (
            item["tbr"],
            item["size_bytes"] or 0,
            item["width"] or 0,
        )

    def _format_label(self, item: dict) -> str:
        return f"{item['height']}p · {item['ext'].upper()}"

    def _select_format(self, info: dict) -> dict:
        if isinstance(info.get("url"), str):
            return info

        formats = [
            item
            for item in info.get("formats") or []
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]
        if not formats:
            return {}

        def score(item: dict) -> tuple:
            protocol = str(item.get("protocol") or "").lower()
            is_http = protocol.startswith("http")
            has_dimensions = bool(item.get("height") or item.get("width"))
            has_video = item.get("vcodec") not in ("none",) and has_dimensions
            has_audio = item.get("acodec") not in ("none",)
            extension = str(item.get("ext") or "").lower()
            return (
                has_video and has_audio,
                is_http,
                has_video,
                extension == "mp4",
                item.get("height") or 0,
                item.get("tbr") or 0,
                item.get("filesize") or item.get("filesize_approx") or 0,
            )

        return max(formats, key=score)

    def _is_http_url(self, value: str) -> bool:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
