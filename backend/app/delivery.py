from urllib.parse import quote

from .models import MediaAsset


def build_delivery_url(asset: MediaAsset, cdn_base_url: str | None) -> str:
    if not cdn_base_url:
        return asset.media_url
    provider = quote(asset.provider, safe="")
    media_id = quote(asset.id, safe="")
    return f"{cdn_base_url}/{provider}/{media_id}"
