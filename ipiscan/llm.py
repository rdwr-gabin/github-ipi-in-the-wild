"""One interface over the two supported model providers.

  openai     reads OPENAI_API_KEY. Defaults are the models used for the published
             results: gpt-5.6-luna (stage 1) and gpt-5.6-terra (stage 2).
  anthropic  reads ANTHROPIC_API_KEY. Defaults: claude-haiku-5-5 (stage 1) and
             claude-sonnet-5-5 (stage 2).

Override either default with --stage1-model / --stage2-model.
"""
from __future__ import annotations

import os

DEFAULT_MODELS = {
    "openai": {1: "gpt-5.6-luna", 2: "gpt-5.6-terra"},
    "anthropic": {1: "claude-haiku-5-5", 2: "claude-sonnet-5-5"},
}
# Output limits used for the published results. READMEs ask for eight verdicts at once.
MAX_OUTPUT_TOKENS = {1: 256, 2: 384, "readme": 1200}


def default_provider() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    raise SystemExit("No model key found: set OPENAI_API_KEY or ANTHROPIC_API_KEY (or run with --detect-only).")


class LLM:
    def __init__(self, provider: str, stage1_model: str | None = None, stage2_model: str | None = None):
        if provider not in DEFAULT_MODELS:
            raise ValueError(f"unknown provider {provider!r}")
        self.provider = provider
        self.models = {1: stage1_model or DEFAULT_MODELS[provider][1], 2: stage2_model or DEFAULT_MODELS[provider][2]}
        if provider == "openai":
            import openai
            self._client = openai.OpenAI(max_retries=5)
        else:
            import anthropic
            self._client = anthropic.Anthropic(max_retries=5)

    def complete(self, stage: int, system: str, user: str, max_tokens: int | None = None) -> str:
        model = self.models[stage]
        max_tokens = max_tokens or MAX_OUTPUT_TOKENS[stage]
        if self.provider == "openai":
            extra = {"temperature": 0, "reasoning_effort": "none"} if stage == 1 else {"reasoning_effort": "low"}
            resp = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_completion_tokens=max_tokens,
                **extra,
            )
            return resp.choices[0].message.content or ""
        resp = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")
