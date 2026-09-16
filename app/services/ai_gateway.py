from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AIProvider:
    name: str
    api_key: str
    model: str


class AIUnavailable(RuntimeError):
    pass


def _provider(name: str) -> AIProvider | None:
    key_attr = f"{name}_api_key"
    model_attr = f"{name}_model"
    if not hasattr(settings, key_attr) or not hasattr(settings, model_attr):
        return None
    secret = getattr(settings, key_attr)
    api_key = secret.get_secret_value().strip()
    model = str(getattr(settings, model_attr)).strip()
    if not api_key or not model:
        return None
    return AIProvider(name=name, api_key=api_key, model=model)


def available_providers(order: list[str] | None = None) -> list[AIProvider]:
    names = order or settings.ai_providers
    result: list[AIProvider] = []
    for name in names:
        provider = _provider(name.strip().lower())
        if provider is not None:
            result.append(provider)
    return result


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("A IA não retornou JSON válido") from None
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("A IA não retornou um objeto JSON")
    return value


async def _openai_compatible(
    client: httpx.AsyncClient,
    *,
    provider: AIProvider,
    endpoint: str,
    system_prompt: str,
    user_prompt: str,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    response = await client.post(
        endpoint,
        headers=headers,
        json={
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 700,
        },
    )
    response.raise_for_status()
    data = response.json()
    return str(data["choices"][0]["message"]["content"])


async def _anthropic(
    client: httpx.AsyncClient,
    *,
    provider: AIProvider,
    system_prompt: str,
    user_prompt: str,
) -> str:
    response = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": provider.model,
            "max_tokens": 700,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
    )
    response.raise_for_status()
    data = response.json()
    for block in data.get("content", []):
        if block.get("type") == "text":
            return str(block.get("text", ""))
    raise ValueError("Resposta Anthropic sem texto")


async def _gemini(
    client: httpx.AsyncClient,
    *,
    provider: AIProvider,
    system_prompt: str,
    user_prompt: str,
) -> str:
    model = provider.model.removeprefix("models/")
    response = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={
            "x-goog-api-key": provider.api_key,
            "content-type": "application/json",
        },
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 700},
        },
    )
    response.raise_for_status()
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("Resposta Gemini sem candidatos")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    if not text:
        raise ValueError("Resposta Gemini sem texto")
    return text


async def _call_provider(
    client: httpx.AsyncClient,
    *,
    provider: AIProvider,
    system_prompt: str,
    user_prompt: str,
) -> str:
    if provider.name == "anthropic":
        return await _anthropic(
            client,
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
    if provider.name == "gemini":
        return await _gemini(
            client,
            provider=provider,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    endpoints = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "xai": "https://api.x.ai/v1/chat/completions",
        "mistral": "https://api.mistral.ai/v1/chat/completions",
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    }
    endpoint = endpoints.get(provider.name)
    if endpoint is None:
        raise ValueError(f"Provedor de IA desconhecido: {provider.name}")
    extra_headers = None
    if provider.name == "openrouter":
        extra_headers = {
            "HTTP-Referer": settings.public_base_url,
            "X-Title": settings.app_name,
        }
    return await _openai_compatible(
        client,
        provider=provider,
        endpoint=endpoint,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        extra_headers=extra_headers,
    )


async def request_structured_ai(
    *,
    system_prompt: str,
    user_prompt: str,
    provider_order: list[str] | None = None,
) -> tuple[dict[str, Any], str]:
    providers = available_providers(provider_order)
    if not providers:
        raise AIUnavailable("Nenhum provedor de IA foi configurado")

    timeout = httpx.Timeout(settings.ai_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for provider in providers:
            try:
                raw = await _call_provider(
                    client,
                    provider=provider,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                return _extract_json(raw), provider.name
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Falha no provedor de IA %s: %s", provider.name, type(exc).__name__)
                continue

    raise AIUnavailable("Todos os provedores de IA configurados falharam")
