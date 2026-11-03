"""Бэкенды LLM: OpenAI-совместимые API (Groq, OpenRouter, локальные) и Anthropic.

OpenAI-совместимый бэкенд написан на стандартной библиотеке (urllib),
поэтому для работы с бесплатными провайдерами ничего устанавливать не нужно.
"""
from __future__ import annotations

import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .prompts import CARD_TOOL, TOOL_NAME, system_prompt

# С запасом: часть бесплатных моделей «рассуждает» и тратит на это токены до ответа.
MAX_TOKENS = 6000
USER_AGENT = "cardgen/1.0"  # без своего User-Agent часть провайдеров отвечает 403


class GenerationError(RuntimeError):
    """Не удалось получить результат от модели."""


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    env_key: str
    default_model: str
    base_url: str | None
    signup: str
    note: str


PROVIDERS: dict[str, ProviderInfo] = {
    "groq": ProviderInfo(
        "groq", "GROQ_API_KEY", "openai/gpt-oss-120b",
        "https://api.groq.com/openai/v1", "https://console.groq.com/keys",
        "бесплатный уровень, карта не нужна",
    ),
    "openrouter": ProviderInfo(
        "openrouter", "OPENROUTER_API_KEY", "nvidia/nemotron-3.5-super-vl:free",
        "https://openrouter.ai/api/v1", "https://openrouter.ai/keys",
        "бесплатные модели с суффиксом :free, список меняется — модель можно задать в CARDGEN_MODEL",
    ),
    "gemini": ProviderInfo(
        "gemini", "GEMINI_API_KEY", "gemini-2.5-flash",
        "https://generativelanguage.googleapis.com/v1beta/openai", "https://aistudio.google.com/apikey",
        "бесплатный уровень; в некоторых странах, включая Россию, недоступен",
    ),
    "custom": ProviderInfo(
        "custom", "CARDGEN_API_KEY", "",
        None, "https://ollama.com/",
        "любой OpenAI-совместимый сервер, например локальная Ollama: CARDGEN_BASE_URL=http://localhost:11434/v1",
    ),
    "anthropic": ProviderInfo(
        "anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-5-5",
        None, "https://console.anthropic.com/",
        "платный; нужен пакет anthropic",
    ),
}
AUTO_ORDER = ("custom", "groq", "openrouter", "gemini", "anthropic")

# Бесплатные модели OpenRouter часто упираются в лимиты провайдера (429). Если модель не задана
# пользователем, отправляем список: OpenRouter сам переключится на следующую доступную.
# OpenRouter разрешает не более 3 моделей в списке, включая основную.
OPENROUTER_FALLBACKS = (
    "xingchen-agi/xing4.0-29b-a4b:free",
    "dots-studio/dots-3-note-preview:free",
)


# ---------------------------------------------------------------- JSON из текста

def parse_json_object(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели (с учётом ```json и <think>)."""
    s = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        start, end = s.find("{"), s.rfind("}")
        if start == -1 or end <= start:
            raise GenerationError("Модель вернула ответ без JSON.") from None
        try:
            obj = json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            raise GenerationError("Модель вернула некорректный JSON.") from None
    if not isinstance(obj, dict):
        raise GenerationError("Модель вернула JSON не в виде объекта.")
    return obj


# ---------------------------------------------------------------- OpenAI-совместимый

class OpenAICompatBackend:
    mode = "json"

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 90.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        extra_body: dict[str, Any] | None = None,
    ):
        self.name = name
        self.model = model
        self.used_model = model  # какая модель реально ответила (важно при резервных моделях)
        self._extra_body = extra_body or {}
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep

    # -- публичный метод
    def complete(self, user_content: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt(self.mode)},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
            "max_tokens": MAX_TOKENS,
            "response_format": {"type": "json_object"},
            **self._extra_body,
        }
        try:
            data = self._post(body)
        except _BadRequest as first:
            # Некоторые модели не поддерживают response_format — пробуем без него.
            body.pop("response_format")
            try:
                data = self._post(body)
            except _BadRequest as second:
                raise GenerationError(f"Ошибка API 400: {second.detail or first.detail}") from None

        self.used_model = str(data.get("model") or self.model) if isinstance(data, dict) else self.model
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise GenerationError("API вернул ответ неожиданной структуры.") from None
        if choice.get("finish_reason") == "length":
            raise GenerationError("Ответ модели обрезан по длине (max_tokens).")
        if not content:
            raise GenerationError("Модель вернула пустой ответ.")
        return parse_json_object(content)

    def _wait(self, seconds: float) -> None:
        if seconds >= 5:
            print(f"  лимит запросов или сбой провайдера, ждём {seconds:.0f} с…", file=sys.stderr)
        self._sleep(seconds)

    # -- HTTP с повторами
    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            request = urllib.request.Request(self._url, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    raise GenerationError("API вернул не-JSON ответ.") from None
                # OpenRouter и подобные иногда отвечают 200, но кладут ошибку провайдера в тело.
                if isinstance(data, dict) and data.get("error") and not data.get("choices"):
                    err = data["error"]
                    message = str(err.get("message") if isinstance(err, dict) else err)[:200]
                    if not last:
                        delay = _delay(429 if "429" in str(err) or "rate" in message.lower() else 500, attempt, None)
                        self._wait(delay)
                        continue
                    raise GenerationError(f"Провайдер вернул ошибку: {message}")
                return data
            except urllib.error.HTTPError as e:
                detail = _error_detail(e)
                code = e.code
                if (code == 429 or code >= 500) and not last:
                    header = e.headers.get("Retry-After") if e.headers else None
                    self._wait(_delay(code, attempt, header))
                    continue
                if code == 400:
                    raise _BadRequest(detail) from None
                if code == 401:
                    raise GenerationError("API отклонил ключ (401). Проверьте ключ в .env.") from None
                if code == 403:
                    raise GenerationError(
                        "Доступ запрещён (403). Возможно, провайдер недоступен из вашей страны или ключ без прав."
                        + (f" Ответ: {detail}" if detail else "")
                    ) from None
                if code == 404:
                    raise GenerationError(
                        f"Модель «{self.model}» или адрес API не найдены (404). Проверьте CARDGEN_MODEL."
                    ) from None
                if code == 429:
                    raise GenerationError(
                        "Превышен лимит запросов (429). У бесплатных тарифов он небольшой: подождите минуту и повторите."
                    ) from None
                raise GenerationError(f"Ошибка API {code}: {detail or e.reason}") from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                if not last:
                    self._sleep(2 * (attempt + 1))
                    continue
                if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower():
                    raise GenerationError("Таймаут запроса к API. Повторите позже.") from None
                raise GenerationError(
                    "Нет соединения с API. Проверьте интернет, VPN или прокси."
                ) from None
        raise GenerationError("Не удалось получить ответ от API.")  # pragma: no cover


class _BadRequest(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    try:
        data = json.loads(raw)
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        return str(err)[:300]
    except (json.JSONDecodeError, AttributeError):
        return raw.strip()[:300]


def _delay(code: int, attempt: int, header: str | None) -> float:
    """Пауза перед повтором. Лимиты бесплатных тарифов действуют минутами, поэтому при 429 ждём дольше."""
    try:
        if header:
            return min(float(header), 60.0)
    except ValueError:
        pass
    if code == 429:
        return min(10.0 * (2**attempt), 60.0)
    return min(2.0 * (2**attempt), 30.0)


# ---------------------------------------------------------------- Anthropic

class AnthropicBackend:
    mode = "tool"
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        try:
            import anthropic
        except ImportError:
            raise GenerationError(
                "Для провайдера anthropic нужен пакет: pip install anthropic"
            ) from None
        self._anthropic = anthropic
        self.model = model
        # SDK сам повторяет запросы при 429/5xx и сетевых сбоях.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=90.0)

    def complete(self, user_content: str) -> dict[str, Any]:
        a = self._anthropic
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system_prompt(self.mode),
                tools=[CARD_TOOL],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{"role": "user", "content": user_content}],
            )
        except a.AuthenticationError:
            raise GenerationError("API отклонил ключ (401). Проверьте ANTHROPIC_API_KEY.") from None
        except a.PermissionDeniedError:
            raise GenerationError("Нет доступа к модели или API (403).") from None
        except a.NotFoundError:
            raise GenerationError(f"Модель «{self.model}» не найдена (404).") from None
        except a.RateLimitError:
            raise GenerationError("Превышен лимит запросов (429). Повторите позже.") from None
        except a.APITimeoutError:
            raise GenerationError("Таймаут запроса к API. Повторите позже.") from None
        except a.APIConnectionError:
            raise GenerationError("Нет соединения с API. Проверьте интернет или прокси.") from None
        except a.APIStatusError as e:
            raise GenerationError(f"Ошибка API {e.status_code}: {e.message}") from None

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise GenerationError("Ответ модели обрезан по длине (max_tokens).")
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
                if isinstance(block.input, dict):
                    return block.input
        raise GenerationError("Модель не вернула структурированный ответ.")


# ---------------------------------------------------------------- выбор провайдера

def pick_provider(requested: str, env: Mapping[str, str]) -> str:
    if requested != "auto":
        if requested not in PROVIDERS:
            raise GenerationError(
                f"Неизвестный провайдер «{requested}». Доступны: {', '.join(PROVIDERS)}."
            )
        return requested
    for name in AUTO_ORDER:
        info = PROVIDERS[name]
        trigger = "CARDGEN_BASE_URL" if name == "custom" else info.env_key
        if env.get(trigger, "").strip():
            return name
    raise GenerationError(
        "Не найден ни один ключ API. Бесплатный вариант: получите ключ на "
        f"{PROVIDERS['groq'].signup} и впишите в .env строку GROQ_API_KEY=... "
        f"(запасной вариант: OPENROUTER_API_KEY, ключ на {PROVIDERS['openrouter'].signup})."
    )


def make_backend(provider: str, model: str | None, env: Mapping[str, str]):
    name = pick_provider(provider, env)
    info = PROVIDERS[name]
    key = env.get(info.env_key, "").strip()
    chosen_model = (model or env.get("CARDGEN_MODEL", "").strip() or info.default_model).strip()

    if name == "custom":
        base_url = env.get("CARDGEN_BASE_URL", "").strip()
        if not base_url:
            raise GenerationError("Для провайдера custom задайте CARDGEN_BASE_URL.")
        if not chosen_model:
            raise GenerationError("Для провайдера custom задайте модель: CARDGEN_MODEL или --model.")
        return OpenAICompatBackend("custom", base_url, key, chosen_model)

    if not key:
        raise GenerationError(f"Не задан {info.env_key}. Ключ можно получить здесь: {info.signup}")
    if name == "anthropic":
        return AnthropicBackend(key, chosen_model)
    extra = None
    if name == "openrouter":
        # Рассуждения отключаем: на бесплатных моделях они съедают весь лимит токенов,
        # и ответ приходит пустым (finish_reason=length). Модели без этой опции параметр игнорируют.
        extra = {"reasoning": {"enabled": False}}
        if chosen_model == info.default_model:
            extra["models"] = [chosen_model, *OPENROUTER_FALLBACKS]
    return OpenAICompatBackend(name, info.base_url, key, chosen_model, extra_body=extra)
