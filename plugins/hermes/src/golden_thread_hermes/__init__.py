"""Golden Thread Hermes plugin entry points.

Exports:
  - register_memory_provider(ctx): registers the Golden Thread MemoryProvider.
  - register(ctx): registers the Observer Hooks guardrail plugin.
"""

from __future__ import annotations

from golden_thread_hermes.memory.provider import GoldenThreadMemoryProvider
from golden_thread_hermes.guardrails import register as register_guardrails


def register_memory_provider(ctx) -> None:
    """Hermes memory-provider discovery entry point."""
    ctx.register_memory_provider(GoldenThreadMemoryProvider())


def register(ctx) -> None:
    """Hermes general-plugin / Observer Hooks entry point."""
    register_guardrails(ctx)
