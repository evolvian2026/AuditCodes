"""Structured calls to Claude, plus a mock for tests.

Every call asks for one JSON object matching a Pydantic schema (structured outputs), so the rest
of the pipeline works with typed values, never with free text. The system prompt carries the rule
catalog and is cached; the per-question content comes after it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

DEFAULT_MODEL = "claude-opus-5"
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    model: str

    def structured(self, *, task: str, system: str, user: str, schema: type[T], max_tokens: int = 16000, effort: str = "high") -> T: ...


@dataclass
class CallRecord:
    task: str
    system: str
    user: str
    schema: str
    output: Any


@dataclass
class MockLLM:
    """Canned responses keyed by task; a response may be a value or a function of (system, user)."""

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    model: str = "mock"

    def structured(self, *, task: str, system: str, user: str, schema: type[T], max_tokens: int = 16000, effort: str = "high") -> T:
        if task not in self.responses:
            raise LLMError(f"mock has no response for task {task!r}")
        resp = self.responses[task]
        if callable(resp):
            resp = resp(system, user)
        out = resp if isinstance(resp, schema) else schema.model_validate(resp)
        self.calls.append(CallRecord(task, system, user, schema.__name__, out))
        return out


class ClaudeLLM:
    def __init__(self, model: str = DEFAULT_MODEL, client: Any = None, max_attempts: int = 2) -> None:
        import anthropic

        self.model = model
        self._client = client or anthropic.Anthropic()
        self._max_attempts = max_attempts

    def structured(self, *, task: str, system: str, user: str, schema: type[T], max_tokens: int = 16000, effort: str = "high") -> T:
        import anthropic

        json_schema = strict_schema(schema.model_json_schema())
        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                with self._client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}],
                    thinking={"type": "adaptive"},
                    output_config={"effort": effort, "format": {"type": "json_schema", "schema": json_schema}},
                ) as stream:
                    message = stream.get_final_message()
            except anthropic.RateLimitError as e:
                last_error = e
                time.sleep(5 * (attempt + 1))
                continue
            except anthropic.APIStatusError as e:
                raise LLMError(f"{task}: API error {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:
                last_error = e
                time.sleep(2 * (attempt + 1))
                continue
            if message.stop_reason == "refusal":
                raise LLMError(f"{task}: the model declined this request")
            if message.stop_reason == "max_tokens":
                raise LLMError(f"{task}: output exceeded {max_tokens} tokens")
            text = next((b.text for b in message.content if b.type == "text"), "")
            try:
                return schema.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as e:
                last_error = e
                continue
        raise LLMError(f"{task}: no valid structured response after {self._max_attempts} attempt(s): {last_error}")


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to structured outputs: closed objects, all keys required."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for key in ("default", "title"):
                node.pop(key, None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        return node

    return walk(schema)


def default_llm(model: str | None = None) -> LLM | None:
    """A Claude client when credentials are configured, else None (LLM stages are skipped)."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    return ClaudeLLM(model or os.environ.get("AUDITCODES_MODEL", DEFAULT_MODEL))
