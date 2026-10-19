from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from .models import Provider
from .providers import match_provider


MAX_URL_LENGTH = 4096
_DEFAULT_PORTS = {"http": 80, "https": 443}


class UrlValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedUrl:
    original_url: str
    normalized_url: str
    provider: Provider | None
    content_id: str | None
    content_type: str | None
    supported: bool
    message: str


def validate_video_url(raw_url: str) -> ValidatedUrl:
    if not isinstance(raw_url, str):
        raise UrlValidationError("invalid_url", "The URL must be a string.")

    original_url = raw_url.strip()
    if not original_url:
        raise UrlValidationError("empty_url", "Enter a video URL.")

    if len(original_url) > MAX_URL_LENGTH:
        raise UrlValidationError(
            "url_too_long",
            f"URLs must be {MAX_URL_LENGTH} characters or fewer.",
        )

    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in original_url):
        raise UrlValidationError(
            "malformed_url",
            "The URL contains whitespace or control characters.",
        )

    try:
        parsed = urlsplit(original_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UrlValidationError("malformed_url", "The URL could not be parsed.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise UrlValidationError(
            "unsupported_scheme",
            "Only HTTP and HTTPS URLs are supported.",
        )

    if not parsed.netloc or not host:
        raise UrlValidationError("malformed_url", "The URL must include a hostname.")

    if parsed.username is not None or parsed.password is not None:
        raise UrlValidationError(
            "credentials_not_allowed",
            "URLs containing credentials are not accepted.",
        )

    if port is not None and port != _DEFAULT_PORTS[scheme]:
        raise UrlValidationError(
            "unsupported_port",
            "Only the default HTTP or HTTPS port is accepted.",
        )

    try:
        normalized_host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise UrlValidationError("malformed_url", "The hostname is not valid.") from exc

    if not normalized_host:
        raise UrlValidationError("malformed_url", "The URL must include a hostname.")

    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise UrlValidationError(
            "host_not_allowed",
            "Local hostnames are not accepted.",
        )

    try:
        ip_address(normalized_host)
    except ValueError:
        pass
    else:
        raise UrlValidationError(
            "ip_address_not_allowed",
            "IP address URLs are not accepted.",
        )

    provider_match = match_provider(normalized_host, parsed.path)

    if provider_match is not None and provider_match.supported and provider_match.content_id:
        normalized_url = f"https://x.com/i/status/{provider_match.content_id}"
    else:
        normalized_url = urlunsplit(
            (
                scheme,
                normalized_host,
                parsed.path or "/",
                parsed.query,
                "",
            )
        )

    return ValidatedUrl(
        original_url=original_url,
        normalized_url=normalized_url,
        provider=provider_match.public if provider_match is not None else None,
        content_id=provider_match.content_id if provider_match is not None else None,
        content_type=provider_match.content_type if provider_match is not None else None,
        supported=provider_match.supported if provider_match is not None else False,
        message=provider_match.message if provider_match is not None else "This provider is not supported.",
    )
