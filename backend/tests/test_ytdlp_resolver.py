import asyncio

import pytest

import my_package.resolvers.ytdlp_resolver as resolver_module
from app.extraction import ExtractionError, load_resolver
from app.settings import DEFAULT_MEDIA_RESOLVER, Settings
from app.url_validation import validate_video_url
from my_package.resolvers.ytdlp_resolver import UnauthenticatedYtdlpResolver


class _FakeDownloadError(Exception):
    pass


class _FakeUtils:
    DownloadError = _FakeDownloadError


def _install_fake_ytdlp(monkeypatch, info):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def extract_info(self, url, download=False):
            captured["url"] = url
            captured["download"] = download
            if isinstance(info, Exception):
                raise info
            return info

    fake_module = type(
        "FakeYtDlp",
        (),
        {"YoutubeDL": FakeYoutubeDL, "utils": _FakeUtils},
    )
    monkeypatch.setattr(resolver_module, "yt_dlp", fake_module)
    return captured


def _validated_url():
    return validate_video_url("https://x.com/immasiddx/status/2112240443249606785/video/1")


def test_resolves_metadata_without_authentication(monkeypatch):
    captured = _install_fake_ytdlp(
        monkeypatch,
        {
            "id": "2112240443249606785",
            "title": "Example video",
            "duration": 12.5,
            "ext": "mp4",
            "url": "https://video.twimg.com/example.mp4",
            "filesize": 4096,
        },
    )

    resolver = UnauthenticatedYtdlpResolver()
    asset = asyncio.run(resolver.resolve(_validated_url()))

    assert asset.id == "2112240443249606785"
    assert asset.provider == "x"
    assert asset.title == "Example video"
    assert asset.media_url == "https://video.twimg.com/example.mp4"
    assert asset.content_type == "video/mp4"
    assert asset.size_bytes == 4096
    assert asset.duration_seconds == 12.5
    assert asset.source_url == "https://x.com/i/status/2112240443249606785"

    options = captured["options"]
    assert captured["download"] is False
    assert captured["url"] == "https://x.com/i/status/2112240443249606785"
    assert options["skip_download"] is True
    assert options["noplaylist"] is True
    assert options["ignoreconfig"] is True
    assert "cookiefile" not in options
    assert "username" not in options
    assert "password" not in options


def test_selects_combined_mp4_format(monkeypatch):
    _install_fake_ytdlp(
        monkeypatch,
        {
            "title": "Clip",
            "formats": [
                {
                    "url": "https://cdn.example/audio.m4a",
                    "ext": "m4a",
                    "vcodec": "none",
                    "acodec": "aac",
                },
                {
                    "url": "https://cdn.example/video.webm",
                    "ext": "webm",
                    "vcodec": "vp9",
                    "acodec": "none",
                    "height": 1080,
                    "filesize": 500,
                },
                {
                    "url": "https://cdn.example/video.mp4",
                    "ext": "mp4",
                    "vcodec": "h264",
                    "acodec": "aac",
                    "height": 720,
                    "filesize": 900,
                },
            ],
        },
    )

    resolver = UnauthenticatedYtdlpResolver()
    asset = asyncio.run(resolver.resolve(_validated_url()))

    assert asset.media_url == "https://cdn.example/video.mp4"
    assert asset.content_type == "video/mp4"
    assert asset.size_bytes == 900


def test_inspect_reports_thumbnail_and_progressive_resolutions(monkeypatch):
    _install_fake_ytdlp(
        monkeypatch,
        {
            "title": "Clip",
            "duration": 12.5,
            "thumbnail": "https://pbs.twimg.com/thumb.jpg",
            "formats": [
                {
                    "format_id": "hls-audio",
                    "url": "https://cdn.example/audio.m3u8",
                    "ext": "mp4",
                    "protocol": "m3u8_native",
                    "vcodec": "none",
                    "height": 0,
                },
                {
                    "format_id": "hls-1080",
                    "url": "https://cdn.example/video.m3u8",
                    "ext": "mp4",
                    "protocol": "m3u8_native",
                    "vcodec": "avc1",
                    "height": 1080,
                },
                {
                    "format_id": "http-360",
                    "url": "https://cdn.example/video-360.mp4",
                    "ext": "mp4",
                    "protocol": "https",
                    "vcodec": None,
                    "acodec": None,
                    "width": 640,
                    "height": 360,
                    "tbr": 832,
                    "filesize_approx": 440232,
                },
                {
                    "format_id": "http-720",
                    "url": "https://cdn.example/video-720.mp4",
                    "ext": "mp4",
                    "protocol": "https",
                    "vcodec": None,
                    "acodec": None,
                    "width": 1280,
                    "height": 720,
                    "tbr": 2176,
                    "filesize_approx": 1151376,
                },
                {
                    "format_id": "http-270",
                    "url": "https://cdn.example/video-270.mp4",
                    "ext": "mp4",
                    "protocol": "https",
                    "vcodec": None,
                    "acodec": None,
                    "width": 480,
                    "height": 270,
                    "tbr": 256,
                    "filesize_approx": 135456,
                },
            ],
        },
    )

    resolver = UnauthenticatedYtdlpResolver()
    details = asyncio.run(resolver.inspect(_validated_url()))

    assert details.title == "Clip"
    assert details.thumbnail_url == "https://pbs.twimg.com/thumb.jpg"
    assert details.duration_seconds == 12.5
    assert details.source_url == "https://x.com/i/status/2112240443249606785"
    assert [item.id for item in details.formats] == ["http-720", "http-360", "http-270"]
    assert details.formats[0].label == "720p · MP4"
    assert details.formats[0].media_url == "https://cdn.example/video-720.mp4"


def test_uses_first_playlist_entry(monkeypatch):
    _install_fake_ytdlp(
        monkeypatch,
        {
            "_type": "playlist",
            "entries": [
                {
                    "title": "First clip",
                    "ext": "mp4",
                    "url": "https://cdn.example/first.mp4",
                }
            ],
        },
    )

    resolver = UnauthenticatedYtdlpResolver()
    asset = asyncio.run(resolver.resolve(_validated_url()))

    assert asset.title == "First clip"
    assert asset.media_url == "https://cdn.example/first.mp4"


def test_wraps_download_errors(monkeypatch):
    _install_fake_ytdlp(monkeypatch, _FakeDownloadError("not available"))

    resolver = UnauthenticatedYtdlpResolver()
    with pytest.raises(ExtractionError) as error:
        asyncio.run(resolver.resolve(_validated_url()))

    assert error.value.code == "extraction_failed"
    assert "not available" not in str(error.value)


def test_reports_missing_media_url(monkeypatch):
    _install_fake_ytdlp(monkeypatch, {"title": "Clip", "formats": []})

    resolver = UnauthenticatedYtdlpResolver()
    with pytest.raises(ExtractionError) as error:
        asyncio.run(resolver.resolve(_validated_url()))

    assert error.value.code == "media_url_missing"


def test_load_resolver_from_required_spec():
    resolver = load_resolver(
        "my_package.resolvers.ytdlp_resolver:UnauthenticatedYtdlpResolver"
    )

    assert isinstance(resolver, UnauthenticatedYtdlpResolver)


def test_load_resolver_rejects_invalid_targets():
    assert load_resolver(None) is None

    with pytest.raises(ValueError):
        load_resolver("invalid")

    with pytest.raises(ValueError):
        load_resolver("my_package.resolvers.ytdlp_resolver:MissingResolver")

    with pytest.raises(ValueError):
        load_resolver("app.providers:PROVIDERS")


def test_default_media_resolver_is_ytdlp():
    assert Settings().media_resolver == DEFAULT_MEDIA_RESOLVER


def test_settings_reads_media_resolver(monkeypatch):
    spec = "my_package.resolvers.ytdlp_resolver:UnauthenticatedYtdlpResolver"
    monkeypatch.setenv("MEDIA_RESOLVER", spec)

    assert Settings.from_env().media_resolver == spec
