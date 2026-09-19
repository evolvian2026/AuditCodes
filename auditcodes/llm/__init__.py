"""Language-model access. The model reads and writes prose and code; it never decides pass/fail."""

from .client import LLM, ClaudeLLM, LLMError, MockLLM, default_llm

__all__ = ["LLM", "ClaudeLLM", "LLMError", "MockLLM", "default_llm"]
