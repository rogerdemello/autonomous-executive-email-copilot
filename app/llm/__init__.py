"""LLM provider abstraction and the model-backed decision paths.

``providers`` is a thin, vendor-neutral wrapper (OpenAI/Azure, Anthropic,
Gemini, Ollama) with cost accounting and a circuit breaker. Everything above it
degrades to the deterministic heuristics in ``app.copilot.policy`` when no
provider is configured, so the product runs with zero credentials.
"""
