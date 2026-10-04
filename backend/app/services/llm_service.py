"""LLM service abstraction (Phase 2, §13).

Every LLM interaction in InvestShield goes through :class:`LLMService`. No module
may construct a provider client or call a model endpoint directly, so the
provider can be swapped by configuration alone (D-004).

Design rules:

* **Never raise into the pipeline.** A missing key, a network failure, a timeout,
  a rate limit or invalid JSON all produce a typed result carrying an error code
  (D-009). Callers decide how to degrade.
* **Structured output is the norm.** :meth:`LLMService.structured_generate`
  constrains the response with a JSON schema and validates the payload with
  Pydantic. Free-text parsing of Markdown is never used.
* **Secrets never leave this module.** The API key is read from settings and is
  never logged or returned.
* **Deterministic by default.** Temperature defaults to ``0.0`` so extraction is
  reproducible.

Groq is called over its OpenAI-compatible HTTP endpoint with ``httpx``, which is
already a dependency. This avoids a heavy framework dependency in the hot path
while still satisfying the "no raw provider calls outside the service layer"
rule; a LangChain provider can be added behind the same interface later.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

#: Error codes surfaced to the pipeline. Never replace these with exception text.
LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
LLM_SERVICE_ERROR = "LLM_SERVICE_ERROR"
LLM_TIMEOUT = "LLM_TIMEOUT"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"
LLM_SCHEMA_VIOLATION = "LLM_SCHEMA_VIOLATION"
LLM_DISABLED = "LLM_DISABLED"

#: Strips a ```json fenced block if a model emits one despite schema mode.
_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

#: Bounded excerpt of a provider error message kept in the logs. Groq's
#: error bodies say things like "model not found", which is the one
#: detail that makes a failed call diagnosable; the message never
#: reaches an API response, where only the fixed wording may appear.
_MAX_ERROR_DETAIL_CHARS = 300


def _provider_error_detail(response: httpx.Response) -> str:
    """Return a bounded excerpt of the provider's own error message.

    Args:
        response: The non-2xx response the provider returned.

    Returns:
        The provider's ``error.message`` field, truncated, or an
        empty string when the body is not the expected shape. These
        messages never carry credentials, so nothing sensitive is
        logged.
    """
    try:
        body: Any = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if not isinstance(error, dict):
        return ""
    message = error.get("message")
    if not isinstance(message, str):
        return ""
    return message[:_MAX_ERROR_DETAIL_CHARS]


def _groq_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Complete a JSON schema for Groq's structured-output mode.

    Groq rejects ``strict: true`` unless **every** object schema
    carries ``additionalProperties: false`` and lists **every**
    property in ``required``. Pydantic's ``model_json_schema()``
    emits neither for fields that carry defaults, so the schema is
    completed here rather than sending a request the provider
    rejects with a 400. Only constraints are added — never
    removed — so the payload the model must return is unchanged
    apart from being fully explicit.

    Args:
        schema: The JSON schema generated for a Pydantic model.

    Returns:
        An equivalent schema that satisfies the provider's
        structured-output validation.
    """
    normalized = dict(schema)
    schema_type = normalized.get("type")
    if schema_type == "object":
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["properties"] = {
                name: _groq_strict_schema(defn)
                for name, defn in properties.items()
            }
            normalized["required"] = sorted(properties)
            normalized["additionalProperties"] = False
    elif schema_type == "array":
        items = normalized.get("items")
        if isinstance(items, dict):
            normalized["items"] = _groq_strict_schema(items)
    definitions = normalized.get("$defs")
    if isinstance(definitions, dict):
        normalized["$defs"] = {
            name: _groq_strict_schema(defn)
            for name, defn in definitions.items()
        }
    return normalized


@dataclass(frozen=True)
class LLMResult:
    """Outcome of a free-text generation call.

    Attributes:
        ok: Whether usable text was produced.
        content: Generated text, or ``None`` on failure.
        error_code: Stable error code when ``ok`` is ``False``.
        model: Model identifier that was requested.
        prompt_tokens: Token usage when reported by the provider.
        completion_tokens: Token usage when reported by the provider.
        duration_ms: Wall-clock duration of the call.
    """

    ok: bool
    content: str | None = None
    error_code: str | None = None
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class StructuredResult:
    """Outcome of a schema-constrained generation call.

    Attributes:
        ok: Whether a schema-valid payload was produced.
        data: Validated Pydantic model instance, or ``None`` on failure.
        error_code: Stable error code when ``ok`` is ``False``.
        model: Model identifier that was requested.
        duration_ms: Wall-clock duration of the call.
    """

    ok: bool
    data: SchemaT | None = None
    error_code: str | None = None
    model: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True)
class _Request:
    """Internal provider request."""

    prompt: str
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    json_schema: dict[str, Any] | None = None
    schema_name: str = "extraction"
    timeout_seconds: int = 30
    max_retries: int = 2


class LLMProvider(ABC):
    """Provider contract. Implementations must not raise into the pipeline."""

    name: str

    @abstractmethod
    def is_available(self) -> bool:
        """Return True when this provider has everything it needs to run."""

    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier this provider will call."""

    @abstractmethod
    def complete(self, request: _Request) -> LLMResult:
        """Perform the call, converting every failure into an error code."""


class GroqProvider(LLMProvider):
    """Groq provider using the OpenAI-compatible chat completions API.

    Args:
        settings: Application settings supplying the key, model and base URL.
    """

    name = "groq"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_available(self) -> bool:
        """Return True when a Groq API key is configured."""
        return bool(self._settings.groq_api_key.strip())

    def model_name(self) -> str:
        """Return the configured Groq model id."""
        return self._settings.groq_model

    def complete(self, request: _Request) -> LLMResult:
        """Call Groq, retrying transient failures and never raising."""
        payload: dict[str, Any] = {
            "model": self._settings.groq_model,
            "messages": self._build_messages(request),
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": request.schema_name,
                    "strict": True,
                    "schema": _groq_strict_schema(request.json_schema),
                },
            }

        headers = {
            "Authorization": f"Bearer {self._settings.groq_api_key}",
            "Content-Type": "application/json",
        }

        last_error = LLM_SERVICE_ERROR
        for attempt in range(request.max_retries + 1):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=request.timeout_seconds) as client:
                    response = client.post(
                        f"{self._settings.groq_base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
            except httpx.TimeoutException:
                last_error = LLM_TIMEOUT
                logger.warning("LLM call timed out", extra={"provider": self.name, "attempt": attempt})
                continue
            except httpx.HTTPError as exc:
                last_error = LLM_SERVICE_ERROR
                logger.warning(
                    "LLM call failed", extra={"provider": self.name, "error_type": type(exc).__name__}
                )
                continue

            duration_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code == 429:
                last_error = LLM_RATE_LIMITED
                logger.warning("LLM rate limited", extra={"provider": self.name})
                continue
            if response.status_code >= 400:
                last_error = LLM_SERVICE_ERROR
                logger.warning(
                    "LLM returned an error status",
                    extra={
                        "provider": self.name,
                        "status": response.status_code,
                        "detail": _provider_error_detail(response),
                    },
                )
                continue

            try:
                body = response.json()
                choice = body["choices"][0]
                content = choice["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                return LLMResult(
                    ok=False,
                    error_code=LLM_INVALID_RESPONSE,
                    model=self.model_name(),
                    duration_ms=duration_ms,
                )

            usage = body.get("usage") or {}
            return LLMResult(
                ok=True,
                content=content,
                model=self.model_name(),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                duration_ms=duration_ms,
            )

        return LLMResult(ok=False, error_code=last_error, model=self.model_name())

    @staticmethod
    def _build_messages(request: _Request) -> list[dict[str, str]]:
        """Assemble the chat messages for a request."""
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.append({"role": "user", "content": request.prompt})
        return messages


class NullProvider(LLMProvider):
    """Provider used when no LLM is configured or calls are disabled.

    It reports unavailability rather than fabricating output, which is what lets
    the pipeline run with no API key and degrade honestly (D-009).
    """

    name = "null"

    def __init__(self, reason: str = LLM_NOT_CONFIGURED) -> None:
        self._reason = reason

    def is_available(self) -> bool:
        """Always False: this provider cannot produce output."""
        return False

    def model_name(self) -> str:
        """Return a placeholder model name."""
        return "none"

    def complete(self, request: _Request) -> LLMResult:
        """Return a typed unavailable result."""
        return LLMResult(ok=False, error_code=self._reason)


@dataclass
class LLMService:
    """Facade over the configured LLM provider.

    Args:
        settings: Optional settings override (used by tests).
        provider: Optional explicit provider override (used by tests to inject
            fakes). When omitted, a provider is built from `settings`.
    """

    settings: Settings = field(default_factory=get_settings)
    provider: LLMProvider | None = None

    def __post_init__(self) -> None:
        """Resolve the provider lazily so an explicit override is respected."""
        if self.settings is None:
            self.settings = get_settings()
        if self.provider is None:
            self.provider = self._build_provider(self.settings)

    @staticmethod
    def _build_provider(settings: Settings) -> LLMProvider:
        """Return the provider named by configuration."""
        if settings.llm_provider == "groq":
            return GroqProvider(settings)
        logger.warning("Unknown LLM provider configured", extra={"provider": settings.llm_provider})
        return NullProvider(LLM_NOT_CONFIGURED)

    @property
    def provider_name(self) -> str:
        """Name of the active provider."""
        return self.provider.name if self.provider else "none"

    @property
    def model(self) -> str | None:
        """Model identifier that will be called, or ``None`` when unavailable."""
        return self.provider.model_name() if self.provider else None

    @property
    def available(self) -> bool:
        """Whether the LLM can be called at all."""
        return bool(self.provider and self.provider.is_available())

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> LLMResult:
        """Generate free text. Used only for human-readable phrasing.

        Args:
            prompt: The user prompt.
            system: Optional system instruction.
            temperature: Sampling temperature; defaults to ``0.0``.
            max_tokens: Optional output cap.
            timeout_seconds: Per-attempt timeout override.
            max_retries: Retry count override.

        Returns:
            An :class:`LLMResult`; never raises.
        """
        if self.provider is None:
            return LLMResult(ok=False, error_code=LLM_NOT_CONFIGURED)
        if not prompt.strip():
            return LLMResult(ok=False, error_code=LLM_SERVICE_ERROR, model=self.model)

        request = _Request(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds or self.settings.groq_timeout_seconds,
            max_retries=self.settings.groq_max_retries if max_retries is None else max_retries,
        )

        logger.info(
            "LLM generate requested",
            extra={
                "provider": self.provider.name,
                "model": self.model,
                "prompt_chars": len(prompt),
            },
        )
        return self.provider.complete(request)

    def structured_generate(
        self,
        prompt: str,
        schema: type[SchemaT],
        *,
        schema_name: str | None = None,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult:
        """Generate a payload constrained to `schema` and validate it.

        The provider is asked for JSON matching the schema; the response is then
        parsed defensively and validated with Pydantic. A payload that fails
        validation yields ``LLM_SCHEMA_VIOLATION`` rather than an exception.

        Args:
            prompt: The user prompt.
            schema: Pydantic model the response must satisfy.
            schema_name: Optional name used in the provider's JSON-schema request.
            system: Optional system instruction.
            temperature: Sampling temperature; defaults to ``0.0``.
            max_tokens: Optional output cap.
            timeout_seconds: Per-attempt timeout override.
            max_retries: Retry count override.

        Returns:
            A :class:`StructuredResult` carrying the validated model or an error
            code. Never raises.
        """
        if self.provider is None:
            return StructuredResult(ok=False, error_code=LLM_NOT_CONFIGURED)
        if not prompt.strip():
            return StructuredResult(ok=False, error_code=LLM_SERVICE_ERROR, model=self.model)

        request = _Request(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            json_schema=schema.model_json_schema(),
            schema_name=schema_name or schema.__name__.lower(),
            timeout_seconds=timeout_seconds or self.settings.groq_timeout_seconds,
            max_retries=self.settings.groq_max_retries if max_retries is None else max_retries,
        )

        logger.info(
            "LLM structured generate requested",
            extra={
                "provider": self.provider.name,
                "model": self.model,
                "schema": schema.__name__,
                "prompt_chars": len(prompt),
            },
        )
        result = self.provider.complete(request)
        if not result.ok or result.content is None:
            return StructuredResult(
                ok=False,
                error_code=result.error_code or LLM_SERVICE_ERROR,
                model=result.model,
                duration_ms=result.duration_ms,
            )

        payload = self._parse_json_object(result.content)
        if payload is None:
            return StructuredResult(
                ok=False,
                error_code=LLM_INVALID_RESPONSE,
                model=result.model,
                duration_ms=result.duration_ms,
            )

        try:
            data = schema.model_validate(payload)
        except ValidationError as exc:
            logger.warning(
                "LLM output failed schema validation",
                extra={"schema": schema.__name__, "errors": exc.error_count()},
            )
            return StructuredResult(
                ok=False,
                error_code=LLM_SCHEMA_VIOLATION,
                model=result.model,
                duration_ms=result.duration_ms,
            )

        return StructuredResult(
            ok=True, data=data, model=result.model, duration_ms=result.duration_ms
        )

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any] | None:
        """Parse a JSON object from a model response.

        Tolerates a fenced code block and leading/trailing prose, but never
        guesses: it returns ``None`` rather than a partial object.

        Args:
            content: Raw model output.

        Returns:
            The parsed object, or ``None`` when parsing fails.
        """
        candidate = content.strip()
        fenced = _FENCE.search(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start == -1 or end <= start:
                return None
            try:
                parsed = json.loads(candidate[start : end + 1])
            except (ValueError, TypeError):
                return None
        return parsed if isinstance(parsed, dict) else None


__all__ = [
    "LLM_DISABLED",
    "LLM_INVALID_RESPONSE",
    "LLM_NOT_CONFIGURED",
    "LLM_RATE_LIMITED",
    "LLM_SCHEMA_VIOLATION",
    "LLM_SERVICE_ERROR",
    "LLM_TIMEOUT",
    "GroqProvider",
    "LLMProvider",
    "LLMResult",
    "LLMService",
    "NullProvider",
    "StructuredResult",
]
