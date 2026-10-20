import asyncio
from urllib.parse import urlsplit

import yt_dlp

from app.extraction import ExtractionError
from app.models import MediaAsset
from app.url_validation import ValidatedUrl


_CONTENT_TYPES = {
    "m3u8": "application/vnd.apple.mpegurl",
    "m4v": "video/mp4",
    "mov": "video/quicktime",
    "mp4": "video/mp4",
    "webm": "video/webm",
}


class UnauthenticatedYtdlpResolver:
    def __init__(self, timeout_seconds: float = 20.0, socket_timeout: float = 15.0):
        self._timeout_seconds = timeout_seconds
        self._socket_timeout = socket_timeout

    async def resolve(self, url: ValidatedUrl) -> MediaAsset:
        if not url.supported or url.provider is None or url.content_id is None:
            raise ExtractionError("unsupported_url", "Unsupported URL.")
        if url.provider.id != "x":
            raise ExtractionError("unsupported_provider", "Only X URLs are supported.")

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._resolve_sync, url),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise ExtractionError(
                "extraction_timeout",
                "Media extraction timed out.",
            ) from exc

    def _resolve_sync(self, url: ValidatedUrl) -> MediaAsset:
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

        return MediaAsset(
            id=url.content_id,
            provider=url.provider.id,
            title=info.get("title") or f"X video {url.content_id}",
            source_url=url.normalized_url,
            media_url=media_url,
            content_type=_CONTENT_TYPES.get(extension, "video/mp4"),
            size_bytes=int(size) if isinstance(size, (int, float)) else None,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        )

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
            has_video = item.get("vcodec") not in (None, "none")
            has_audio = item.get("acodec") not in (None, "none")
            extension = str(item.get("ext") or "").lower()
            return (
                has_video and has_audio,
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
