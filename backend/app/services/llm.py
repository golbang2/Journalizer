"""One way to ask an LLM for structured JSON, whichever provider the company connected.

Providers: Claude (Anthropic SDK) and GPT (OpenAI REST). Every call takes a
system prompt, user parts (text and files such as PDF or images) and a JSON schema, and returns
the parsed JSON. OpenAI model names are never hardcoded: they come from the
provider's model list when the key is registered, and the company picks one.
"""
from dataclasses import dataclass
import base64
import json
import os
import re
import time

import anthropic
import httpx

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "env": ("ANTHROPIC_API_KEY",), "console": "console.anthropic.com", "key_hint": "sk-ant-..."},
    "openai": {"label": "GPT (OpenAI)", "env": ("OPENAI_API_KEY",), "console": "platform.openai.com", "key_hint": "sk-..."},
}
DEFAULT_ANTHROPIC_MODEL = os.environ.get("CLAUDE_JUDGE_MODEL", "claude-haiku-5-5")
OPENAI_URL = "https://api.openai.com/v1"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


class LlmError(RuntimeError):
    pass


@dataclass(frozen=True)
class Client:
    provider: str
    api_key: str = ""
    model: str = ""

    @property
    def label(self) -> str:
        return f"{PROVIDERS[self.provider]['label'].split(' ')[0]} {self.model}"


@dataclass(frozen=True)
class File:
    name: str
    media_type: str
    data: bytes

    @property
    def kind(self) -> str:
        if self.name.lower().endswith(".pdf") or self.media_type == "application/pdf":
            return "pdf"
        return "image"

    @property
    def image_type(self) -> str:
        return self.media_type if self.media_type in IMAGE_TYPES else "image/jpeg"

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode()


def env_key(provider: str) -> str:
    return next((os.environ[name] for name in PROVIDERS[provider]["env"] if os.environ.get(name)), "")


# --- Completion ---------------------------------------------------------------------------------

def complete(client: Client, system: str, parts: list, schema: dict, effort: str = "medium", max_tokens: int = 16000, timeout: float = 180) -> dict:
    """Ask the model and return the JSON object it produced (validated as JSON, not against the schema)."""
    call = {"anthropic": _anthropic, "openai": _openai}.get(client.provider)
    if call is None:
        raise LlmError(f"지원하지 않는 AI 서비스입니다: {client.provider}")
    if not client.model:
        raise LlmError("사용할 모델을 고르지 않았습니다. 메인 화면의 ‘AI 연결’에서 모델을 선택하세요.")
    text = call(client, system, parts, schema, effort, max_tokens, timeout)
    try:
        value = json.loads(_strip_fence(text))
    except json.JSONDecodeError as exc:
        raise LlmError("모델 응답을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise LlmError("모델 응답 형식이 맞지 않습니다.")
    return value


def _strip_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text or "")
    return match.group(1) if match else (text or "").strip()


def _anthropic(client: Client, system: str, parts: list, schema: dict, effort: str, max_tokens: int, timeout: float) -> str:
    content = []
    for part in parts:
        if isinstance(part, File):
            source = {"type": "base64", "media_type": "application/pdf" if part.kind == "pdf" else part.image_type, "data": part.b64}
            content.append({"type": "document" if part.kind == "pdf" else "image", "source": source})
        else:
            content.append({"type": "text", "text": part})
    try:
        response = anthropic.Anthropic(api_key=client.api_key or None, max_retries=3, timeout=timeout).messages.create(
            model=client.model, max_tokens=max_tokens, system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": content}],
            # SDK 0.49 predates output_config, so it goes in the request body directly.
            extra_body={"output_config": {"effort": effort, "format": {"type": "json_schema", "schema": schema}}},
        )
    except anthropic.AuthenticationError as exc:
        raise LlmError("Claude API 인증에 실패했습니다. 메인 화면의 ‘AI 연결’에서 API 키를 다시 등록하세요.") from exc
    except anthropic.RateLimitError as exc:
        raise LlmError("Claude API 요청 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.") from exc
    except anthropic.APIStatusError as exc:
        raise LlmError(f"Claude API 오류 ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LlmError("Claude API에 연결하지 못했습니다. 네트워크를 확인하세요.") from exc
    except TypeError as exc:  # no credentials configured at all
        raise LlmError("API 키가 없습니다. 메인 화면의 ‘AI 연결’에서 API 키를 등록하세요.") from exc
    if response.stop_reason == "refusal":
        raise LlmError("모델이 이 요청의 판단을 거절했습니다.")
    if response.stop_reason == "max_tokens":
        raise LlmError("모델 응답이 길이 제한에 걸렸습니다. 항목 수를 줄여 다시 시도하세요.")
    return next((b.text for b in response.content if b.type == "text"), "")


def _post(url: str, headers: dict, body: dict, timeout: float, service: str) -> dict:
    """POST with a few retries on rate limits and server errors; readable errors otherwise."""
    for attempt in range(4):
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            if attempt == 3:
                raise LlmError(f"{service} API에 연결하지 못했습니다. 네트워크를 확인하세요.") from exc
            time.sleep(2 ** attempt)
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(float(response.headers.get("retry-after") or 2 ** attempt))
            continue
        if response.status_code in (401, 403):
            raise LlmError(f"{service} API 인증에 실패했습니다. 메인 화면의 ‘AI 연결’에서 API 키를 다시 등록하세요.")
        if response.status_code == 429:
            raise LlmError(f"{service} API 요청 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.")
        if response.status_code >= 400:
            raise LlmError(f"{service} API 오류 ({response.status_code}): {_error_text(response)}")
        return response.json()
    raise LlmError(f"{service} API가 응답하지 않습니다.")


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        return (error.get("message") if isinstance(error, dict) else str(error or body))[:200]
    except ValueError:
        return response.text[:200]


def _openai(client: Client, system: str, parts: list, schema: dict, effort: str, max_tokens: int, timeout: float) -> str:
    content = []
    for part in parts:
        if isinstance(part, File) and part.kind == "pdf":
            content.append({"type": "file", "file": {"filename": part.name or "document.pdf", "file_data": f"data:application/pdf;base64,{part.b64}"}})
        elif isinstance(part, File):
            content.append({"type": "image_url", "image_url": {"url": f"data:{part.image_type};base64,{part.b64}"}})
        else:
            content.append({"type": "text", "text": part})
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
    body = {"model": client.model, "messages": messages, "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}}
    data = _post(f"{OPENAI_URL}/chat/completions", {"Authorization": f"Bearer {client.api_key}"}, body, timeout, "OpenAI")
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    if message.get("refusal"):
        raise LlmError("모델이 이 요청의 판단을 거절했습니다.")
    if choice.get("finish_reason") == "length":
        raise LlmError("모델 응답이 길이 제한에 걸렸습니다. 항목 수를 줄여 다시 시도하세요.")
    return message.get("content") or ""


# --- Keys and models ----------------------------------------------------------------------------

def _version_key(model: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", model)]


def list_models(provider: str, api_key: str) -> list[str]:
    """Models this key can use for text and documents. Also proves the key works."""
    if provider == "anthropic":
        try:
            page = anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=20).models.list(limit=100)
        except anthropic.AuthenticationError as exc:
            raise LlmError("API 키가 올바르지 않습니다. Claude Console에서 발급한 키인지 확인하세요.") from exc
        except anthropic.APIConnectionError as exc:
            raise LlmError("Claude API에 연결하지 못했습니다. 네트워크를 확인하세요.") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError(f"Claude API 오류 ({exc.status_code}): {exc.message}") from exc
        ids = [m.id for m in page.data if m.id.startswith("claude")]
    elif provider == "openai":
        data = _get(f"{OPENAI_URL}/models", {"Authorization": f"Bearer {api_key}"}, "OpenAI")
        skip = ("audio", "realtime", "tts", "transcribe", "image", "search", "embedding", "instruct", "dall", "whisper", "moderation")
        ids = [m["id"] for m in data.get("data", []) if re.match(r"^(gpt-|o\d|chatgpt)", m["id"]) and not any(s in m["id"] for s in skip)]
    else:
        raise LlmError(f"지원하지 않는 AI 서비스입니다: {provider}")
    return sorted(set(ids), key=_version_key, reverse=True)


def _get(url: str, headers: dict, service: str) -> dict:
    try:
        response = httpx.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        raise LlmError(f"{service} API에 연결하지 못했습니다. 네트워크를 확인하세요.") from exc
    if response.status_code in (400, 401, 403):
        raise LlmError(f"API 키가 올바르지 않습니다. {service}에서 발급한 키인지 확인하세요.")
    if response.status_code >= 400:
        raise LlmError(f"{service} API 오류 ({response.status_code}): {_error_text(response)}")
    return response.json()


def default_model(provider: str, models: list[str]) -> str:
    """A sensible, inexpensive pick from the list: Claude Haiku or a GPT mini."""
    if not models:
        return DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else ""
    patterns = {
        "anthropic": [re.escape(DEFAULT_ANTHROPIC_MODEL) + "$", r"^claude-haiku", r"^claude-"],
        "openai": [r"^gpt-[\d.]+-mini$", r"^gpt-[\d.]+$", r"^gpt-"],
    }[provider]
    for pattern in patterns:
        found = [m for m in models if re.search(pattern, m)]
        if found:
            return found[0]  # models are newest first
    return models[0]


def mask(key: str) -> str:
    return f"{key[:7]}…{key[-4:]}" if len(key) > 12 else "…"
