import re
from dataclasses import dataclass

from .models import Provider


@dataclass(frozen=True)
class ProviderDefinition:
    id: str
    name: str
    hosts: frozenset[str]


@dataclass(frozen=True)
class ProviderMatch:
    definition: ProviderDefinition
    content_id: str | None
    content_type: str | None
    supported: bool
    message: str

    @property
    def public(self) -> Provider:
        return Provider(id=self.definition.id, name=self.definition.name)


X_PROVIDER = ProviderDefinition(
    id="x",
    name="X (Twitter)",
    hosts=frozenset(
        {
            "x.com",
            "www.x.com",
            "mobile.x.com",
            "twitter.com",
            "www.twitter.com",
            "mobile.twitter.com",
            "m.twitter.com",
        }
    ),
)

PROVIDERS = (X_PROVIDER,)
_PROVIDERS_BY_ID = {provider.id: provider for provider in PROVIDERS}
_STATUS_PATH = re.compile(
    r"^/(?:(?:[A-Za-z0-9_]{1,15}/status(?:es)?)|(?:i/status)|(?:i/web/status))"
    r"/(?P<content_id>[0-9]{1,25})(?:/video/[0-9]{1,3})?/?$"
)


def get_provider(provider_id: str) -> ProviderDefinition | None:
    return _PROVIDERS_BY_ID.get(provider_id)


def match_provider(host: str, path: str) -> ProviderMatch | None:
    definition = next(
        (provider for provider in PROVIDERS if host in provider.hosts),
        None,
    )
    if definition is None:
        return None

    status_match = _STATUS_PATH.fullmatch(path)
    if status_match is None:
        return ProviderMatch(
            definition=definition,
            content_id=None,
            content_type=None,
            supported=False,
            message="This X link is not a video status URL.",
        )

    return ProviderMatch(
        definition=definition,
        content_id=status_match.group("content_id"),
        content_type="status",
        supported=True,
        message="X video status URL detected.",
    )
