"""Generic paced text completion over LiteLLM, for eval tooling.

`extract_llm.py` already wraps litellm, but its `_complete` is bound to the L3
edge JSON schema and its retry latch is about structured-output support. Eval
tooling needs plain prompted text — generating a candidate question, judging
whether a passage qualifies an answer — and CLAUDE.md's code conventions forbid
importing a vendor SDK outside an adapter module. So the boundary is kept by
adding a second adapter rather than reaching for litellm from a script.

Pacing exists for the same reason it does in `extract_llm`: a free-tier
tokens-per-minute cap is hit by burst, not by daily volume.
"""

from __future__ import annotations

import time
from typing import Any, cast

from dge.adapters.extract_llm import DEFAULT_MODEL, ExtractorError, _is_transient


class TextCompleter:
    """Plain text in, plain text out, with client-side pacing.

    Deliberately not a `dge.interfaces` Protocol implementation: nothing in the
    engine depends on this. It exists for scripts under `scripts/`, which is why
    it stays out of the ingest/retrieval paths entirely.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout: float = 90.0,
        min_interval_s: float = 0.0,
    ) -> None:
        self.model = model
        self.model_id = f"litellm:{model}"
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout = timeout
        self._min_interval_s = min_interval_s
        self._next_call_at = 0.0

    def _pace(self) -> None:
        if self._min_interval_s <= 0.0:
            return
        wait = self._next_call_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._next_call_at = time.monotonic() + self._min_interval_s

    def complete(self, system: str, user: str) -> str:
        """One completion. Raises `ExtractorError` rather than returning a
        sentinel, so a caller that forgets to check cannot silently record an
        empty question as a real one."""
        import litellm  # lazy: `dge[llm]` is an optional extra

        self._pace()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "timeout": self._timeout,
            "num_retries": self._max_retries,
        }
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            raise ExtractorError(
                f"{self.model}: completion failed "
                f"({'transient' if _is_transient(exc) else 'permanent'}): {exc}"
            ) from exc

        content = response.choices[0].message.content
        if not content or not content.strip():
            raise ExtractorError(f"{self.model}: empty response")
        return cast(str, content).strip()
