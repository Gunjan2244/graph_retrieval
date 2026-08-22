"""L3 edge extractor over LiteLLM.

The only module in the codebase that knows litellm exists (CLAUDE.md code
conventions). Everything model-shaped about L3 — the prompt program, the closed
enum, the evidence check, the cost gate — lives in `dge.l3` and is exercised by
`tests/test_l3_extract.py` against a fake extractor with no key and no network.
This file is transport.

Provider is configuration, not a code path
------------------------------------------
`model` is a LiteLLM model string and nothing here branches on it:

    groq/llama-3.3-70b-versatile     GROQ_API_KEY       free tier, fast
    gemini/gemini-3.6-flash          GEMINI_API_KEY     free tier, generous
    ollama/llama3.1                  none               local, no key at all
    openai/gpt-4o-mini               OPENAI_API_KEY

Structured output, and what happens when a provider does not have it
--------------------------------------------------------------------
The request asks for `json_schema` with `strict` — the closed enums in
`dge.l3.schema.response_json_schema` then make a fabricated unit label
unrepresentable rather than merely invalid. Providers that do not support it
(most local Ollama builds, some Groq models) raise, and this falls back to
plain JSON object mode with the schema inlined in the prompt.

The fallback is a QUALITY degradation, never a SAFETY one. Nothing downstream
trusts the response shape: `dge.l3.run.validate_candidate` re-checks the type
against the closed enum, re-checks both endpoints against the window it built,
and re-checks the evidence span verbatim — so a provider that ignores the
schema entirely can produce junk that gets discarded, never junk that gets
stored.

`prompt_hash` deliberately does NOT depend on which provider answered. It
identifies the instructions; `model_id` identifies who followed them. Both are
stamped on every edge (CLAUDE.md invariant 5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, cast

from dge.interfaces import EdgeCandidate
from dge.l3.prompt import build_messages, prompt_hash, ref_labels
from dge.l3.schema import ExtractionResponse, response_json_schema
from dge.model import Node

# Verified reachable on 2026-08-21 with native `json_schema` + `strict`, and a
# 1000 requests/day free tier. The previous default,
# `groq/llama-3.3-70b-versatile`, now 404s ("does not exist or you do not have
# access to it") — the same staleness that retired `gemini-2.0-flash`. A model
# id is data, so it is checked by asking the provider, never assumed.
DEFAULT_MODEL = "groq/openai/gpt-oss-120b"
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _is_transient(exc: Exception) -> bool:
    """Is this "the provider is busy" rather than "the provider cannot do this"?

    The structured-output latch below is one-way, so what trips it matters. A
    429 or a 503 says nothing at all about schema support, and treating one as
    proof of it degraded an entire real corpus run to JSON mode after a single
    rate limit — which then produced a response using `rel_type` instead of
    `type` and lost the section outright. Transient failures propagate to
    `dge.l3.run`, which records them as a failed section; only a request the
    provider actually rejected flips the latch.

    Read off `status_code` when the exception carries one (LiteLLM's exception
    hierarchy does, whatever the vendor) and fall back to the class name, so
    this stays vendor-neutral and needs no litellm import at module scope.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    name = type(exc).__name__
    return name in {
        "RateLimitError", "ServiceUnavailableError", "InternalServerError",
        "APIConnectionError", "APIError", "Timeout", "APITimeoutError",
    }


class ExtractorError(RuntimeError):
    pass


def _strip_fence(text: str) -> str:
    """Models in JSON-object mode fence their output surprisingly often."""
    return _FENCE_RE.sub("", text).strip()


class LiteLLMEdgeExtractor:
    """`dge.interfaces.EdgeExtractor` over any LiteLLM-routable model."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout: float = 90.0,
    ) -> None:
        self.model = model
        self.model_id = f"litellm:{model}"
        self.prompt_hash = prompt_hash()
        self._temperature = temperature
        self._max_retries = max_retries
        self._timeout = timeout
        self._structured_output = True

    def _complete(self, messages: list[dict[str, str]], labels: Sequence[str]) -> str:
        import litellm  # imported lazily: `dge[llm]` is an optional extra

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self._temperature,
            "timeout": self._timeout,
            "num_retries": self._max_retries,
        }
        if self._structured_output:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dge_l3_edges",
                    "schema": response_json_schema(labels),
                    "strict": True,
                },
            }
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            if not self._structured_output or _is_transient(exc):
                # A rate limit, a 503 or a dead socket is not evidence about
                # schema support. Let it through as a failed section rather
                # than spending a second call AND permanently downgrading
                # every remaining call in the run.
                raise
            # One-way latch: a provider that cannot do json_schema will not
            # start being able to mid-run, and retrying it per section would
            # double the bill for every remaining call.
            self._structured_output = False
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = [
                *messages[:-1],
                {"role": "user", "content": messages[-1]["content"]
                 + "\n\nRespond with JSON matching this schema:\n"
                 + json.dumps(response_json_schema(labels))},
            ]
            try:
                response = litellm.completion(**kwargs)
            except Exception as fallback_exc:
                raise ExtractorError(
                    f"{self.model}: structured output failed ({exc}) and JSON mode "
                    f"also failed ({fallback_exc})"
                ) from fallback_exc

        content = response.choices[0].message.content
        if not content:
            raise ExtractorError(f"{self.model}: empty response")
        return cast(str, content)

    def extract(
        self, section_nodes: Sequence[Node], section_path: str, doc_summary: str
    ) -> Sequence[EdgeCandidate]:
        if not section_nodes:
            return ()
        labels = ref_labels(section_nodes)
        messages = build_messages(section_nodes, section_path, doc_summary)
        raw = self._complete(messages, list(labels))

        try:
            parsed = ExtractionResponse.model_validate_json(_strip_fence(raw))
        except ValueError as exc:
            raise ExtractorError(f"{self.model}: unparseable response: {exc}") from exc

        candidates: list[EdgeCandidate] = []
        for edge in parsed.edges:
            src = labels.get(edge.src_ref)
            dst = labels.get(edge.dst_ref)
            if src is None or dst is None:
                # An unresolvable label is dropped here rather than passed on
                # as a fabricated node id. `dge.l3.run` would catch it anyway —
                # it re-checks endpoints against the window — but an adapter
                # should not hand core logic something it knows is wrong.
                continue
            candidates.append(EdgeCandidate(
                src=src,
                dst=dst,
                type=edge.type,
                evidence_span=edge.evidence_span,
                confidence=edge.confidence,
            ))
        return candidates
