import pytest

from app.url_validation import UrlValidationError, validate_video_url


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/opencode/status/1234567890",
        "https://www.twitter.com/opencode/status/1234567890?s=20",
        "https://mobile.x.com/opencode/status/1234567890/",
        "https://twitter.com/i/web/status/1234567890",
        "https://x.com/i/status/1234567890",
        "https://x.com./opencode/status/1234567890",
        "https://x.com:443/opencode/status/1234567890",
        "http://x.com:80/opencode/status/1234567890",
        "HTTPS://X.COM/OpenCode/status/1234567890",
    ],
)
def test_accepts_x_status_urls(url):
    result = validate_video_url(url)

    assert result.supported is True
    assert result.provider is not None
    assert result.provider.id == "x"
    assert result.content_id == "1234567890"
    assert result.content_type == "status"
    assert result.normalized_url == "https://x.com/i/status/1234567890"


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("", "empty_url"),
        ("   ", "empty_url"),
        ("https://x.com/" + "a" * 5000, "url_too_long"),
        ("not a url", "malformed_url"),
        ("ftp://x.com/opencode/status/1", "unsupported_scheme"),
        ("javascript:alert(1)", "unsupported_scheme"),
        ("https://", "malformed_url"),
        ("https://x.com:abc/opencode/status/1", "malformed_url"),
        ("https://[::1", "malformed_url"),
        ("https://x.com:8443/opencode/status/1", "unsupported_port"),
        ("https://x.com:80/opencode/status/1", "unsupported_port"),
        ("https://user:pass@x.com/opencode/status/1", "credentials_not_allowed"),
        ("https://@x.com/opencode/status/1", "credentials_not_allowed"),
        ("https://x.com.evil.com@x.com/opencode/status/1", "credentials_not_allowed"),
        ("https://127.0.0.1/opencode/status/1", "ip_address_not_allowed"),
        ("https://[::1]/opencode/status/1", "ip_address_not_allowed"),
        ("http://localhost/opencode/status/1", "host_not_allowed"),
        ("https://x.com/opencode/status/1\nfoo", "malformed_url"),
        ("https://x.com/opencode /status/1", "malformed_url"),
    ],
)
def test_rejects_invalid_urls(url, code):
    with pytest.raises(UrlValidationError) as error:
        validate_video_url(url)

    assert error.value.code == code


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com.evil.com/opencode/status/1",
        "https://evil.com/x.com/opencode/status/1",
        "https://notx.com/opencode/status/1",
        "https://evilx.com/opencode/status/1",
    ],
)
def test_deceptive_hostnames_are_not_treated_as_x(url):
    result = validate_video_url(url)

    assert result.supported is False
    assert result.provider is None


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/immasiddx/status/2112240443249606785/video/1",
        "https://twitter.com/immasiddx/status/2112240443249606785/video/1?t=abc",
        "https://mobile.x.com/immasiddx/status/2112240443249606785/video/1/",
        "https://x.com/i/status/2112240443249606785/video/1",
        "https://x.com/i/web/status/2112240443249606785/video/1",
    ],
)
def test_accepts_x_video_path_variants(url):
    result = validate_video_url(url)

    assert result.supported is True
    assert result.content_id == "2112240443249606785"
    assert result.normalized_url == "https://x.com/i/status/2112240443249606785"


def test_x_non_status_url_is_identified_but_not_supported():
    result = validate_video_url("https://x.com/opencode")

    assert result.supported is False
    assert result.provider is not None
    assert result.provider.id == "x"
    assert result.content_id is None
    assert "not a video status" in result.message


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/opencode/status/not-a-number",
        "https://x.com/opencode/status/",
        "https://x.com/opencode/status/123/extra",
        "https://x.com/opencode/status/123/video/abc",
        "https://x.com/opencode/status/123/video/1/extra",
        "https://x.com/this_username_is_too_long/status/123",
    ],
)
def test_invalid_x_status_paths_are_not_supported(url):
    result = validate_video_url(url)

    assert result.supported is False
    assert result.provider is not None


def test_unknown_provider_keeps_url_but_marks_unsupported():
    result = validate_video_url("https://example.com/videos/123")

    assert result.supported is False
    assert result.provider is None
    assert result.normalized_url == "https://example.com/videos/123"
    assert result.message == "This provider is not supported."
