from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings


TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    model_override: str = ""


class ProviderFailure(Exception):
    def __init__(self, status_code: int | None, error_code: str):
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = status_code is None or status_code in TRANSIENT_STATUSES
        super().__init__(error_code)


def configured_providers() -> list[Provider]:
    if not settings.primary_base_url or not settings.primary_api_key:
        return []
    providers: list[Provider] = [Provider("primary", settings.primary_base_url, settings.primary_api_key)]
    if settings.fallback_base_url and settings.fallback_api_key:
        providers.append(
            Provider("fallback", settings.fallback_base_url, settings.fallback_api_key, settings.fallback_model)
        )
    return providers


async def create_chat_completion(provider: Provider, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    body = dict(payload)
    body["stream"] = False
    if provider.model_override:
        body["model"] = provider.model_override

    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=settings.provider_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ProviderFailure(None, "provider_timeout") from exc
    except httpx.RequestError as exc:
        raise ProviderFailure(None, "provider_unavailable") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise ProviderFailure(response.status_code, "provider_rejected_request")
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderFailure(502, "provider_invalid_response") from exc
    if not isinstance(data, dict):
        raise ProviderFailure(502, "provider_invalid_response")
    return data, response.status_code
