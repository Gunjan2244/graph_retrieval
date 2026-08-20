"""L1 normalize: deterministic fallback implementation of the `Normalizer` protocol.

The real L1 stage (coref resolution, self-contained restatement) needs a small
instruct model — see docs/05-engine-implementation.md 5.3. That model is not
wired into the offline core path (no vendor SDK without an adapter, per
CLAUDE.md code conventions), so this module does only what is honestly
achievable without one:

  - inherited context: section_path is carried over from the parser.
  - assertive flag: heuristic (headings and illustration/example text are
    non-assertive; everything else defaults to assertive).
  - normalized text: whitespace/enumeration-prefix cleanup only. This is NOT a
    restatement — no coref is resolved. `raw` is untouched either way
    (CLAUDE.md invariant 2).

Swap in a real model by implementing `Normalizer` against an LLM adapter; nodes
already carry `layer1_version`/`model_id` so a later real pass can supersede
this one without ambiguity about what produced what.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from dge.model import Node

MODEL_ID = "deterministic-heuristic-v1"

_ENUM_PREFIX = re.compile(r"^\s*(?:\(\d+\)|\([a-z]{1,2}\)|\d+\.)\s*")
_WHITESPACE = re.compile(r"\s+")
_NON_ASSERTIVE_MARKERS = re.compile(
    r"^\s*(?:Illustrations?|Examples?)\s*[\-—–.:]", re.IGNORECASE,
)


class DeterministicNormalizer:
    """Offline `Normalizer`: cleanup + heuristics, no model call."""

    def normalize(self, nodes: Sequence[Node], doc_summary: str) -> Sequence[Node]:
        return [self._normalize_one(n) for n in nodes]

    def _normalize_one(self, node: Node) -> Node:
        cleaned = _WHITESPACE.sub(" ", _ENUM_PREFIX.sub("", node.raw)).strip()
        is_assertive = node.is_assertive and not bool(_NON_ASSERTIVE_MARKERS.match(node.raw))
        return replace(
            node,
            normalized=cleaned if cleaned != node.raw else None,
            is_assertive=is_assertive,
            layer1_version=MODEL_ID,
            model_id=MODEL_ID,
        )
