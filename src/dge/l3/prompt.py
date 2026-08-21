"""The L3 prompt program, and its stable hash.

Kept out of the adapter on purpose. The prompt is not vendor-specific — Groq,
Gemini and a local Ollama model all get the identical program — and
`prompt_hash` is half of an edge's version key (CLAUDE.md invariant 5, docs/05
5.1's `(substrate_hash, layer, layer_version, model_id, prompt_hash)`). If the
hash were computed inside an adapter, two adapters could stamp the same hash on
edges produced by different instructions, and the version key would be a lie.

Stdlib only: this module is on the path `dge.pipeline` takes, and the
deterministic pipeline must keep running with no optional dependency installed.

The prompt asks for verbatim evidence spans. It is worth being explicit that
this is a *convenience*, not a control: `dge.l3.evidence` discards spans that
are not verbatim regardless of what the prompt said, because CLAUDE.md
invariant 10 says to enforce in code, not in the prompt. If the wording below
were deleted entirely, the system would get worse recall and exactly the same
soundness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from dge.model import EdgeType, Node, NodeKind

PROMPT_VERSION = "l3-edges-v1"

# The closed enum. BUILD_PLAN Phase 3 is explicit: "Three edge types only:
# supersedes, exception_of, defines." That is a scope decision, not an
# oversight — every additional type is another way for the model to be
# confidently wrong, and the three here are the ones the soundness guarantee
# actually rests on. The legal pack's richer vocabulary (`overrides`,
# `distinguished_by`, ...) is reachable from pattern edges, which are evidenced
# by a drafting convention rather than by a model's judgement.
NO_RELATION = "none"
ALLOWED_EDGE_TYPES: tuple[str, ...] = (
    EdgeType.EXCEPTION_OF.value,
    EdgeType.SUPERSEDES.value,
    EdgeType.DEFINES.value,
    NO_RELATION,
)

_TYPE_GUIDE = """\
exception_of  - the SOURCE carves an exception, proviso or carve-out out of the
                TARGET. Direction matters: the exception points AT the rule it
                modifies, never the reverse.
supersedes    - the SOURCE overrides, displaces, repeals or amends the TARGET.
defines       - the SOURCE uses a term whose meaning the TARGET fixes (a
                definition, an Explanation, or a deeming provision). Direction
                runs from the USE to the DEFINITION.
none          - no relation of the above kinds holds. Returning an empty edge
                list, or a single edge of type "none", are both correct and are
                the expected answer for most sections."""

SYSTEM_PROMPT = f"""\
You extract typed relations between numbered units of a legal document. You do \
not summarise, interpret, or answer questions about the text.

You will be shown ONE section of ONE document. Every unit in it is labelled \
[N1], [N2], and so on.

Return relations that hold BETWEEN UNITS SHOWN IN THIS WINDOW. Use only these \
types:

{_TYPE_GUIDE}

Rules:
1. src_ref and dst_ref must both be labels shown in this window. Never invent a \
label, and never refer to a section that is not displayed.
2. evidence_span must be copied CHARACTER FOR CHARACTER from the text shown. Do \
not paraphrase it, do not tidy it, do not shorten it to a few words. If you \
cannot copy a span that shows the relation, do not report the relation.
3. Report a relation only where the text states it. A relation that is merely \
plausible, or that follows from what you know about the law generally, is not a \
relation in this document.
4. Most sections contain no relation of these types. Returning {{"edges": []}} \
is a correct and common answer. Do not manufacture relations to appear useful.
5. confidence is your own estimate between 0 and 1 that this relation holds as \
stated in the text."""

_USER_TEMPLATE = """\
DOCUMENT: {doc_summary}
SECTION: {section_path}

{window}

Return JSON matching the schema. Relations between the units above only."""


def ref_labels(section_nodes: Sequence[Node]) -> dict[str, str]:
    """label -> node_id, assigned by position.

    Positional and therefore stable for a given window, which is what lets the
    prompt (here) and the reference resolution (in the adapter) agree without
    passing a mapping around. Node ids are deliberately NOT shown to the model:
    a label the model cannot guess is a label it cannot fabricate an edge to.
    """
    return {f"N{i + 1}": n.node_id for i, n in enumerate(section_nodes)}


def render_window(section_nodes: Sequence[Node]) -> str:
    labels = ref_labels(section_nodes)
    by_id: Mapping[str, str] = {v: k for k, v in labels.items()}
    lines = []
    for node in section_nodes:
        marker = " (heading)" if node.kind is NodeKind.STRUCTURAL else ""
        lines.append(f"[{by_id[node.node_id]}]{marker} {node.raw}")
    return "\n\n".join(lines)


def build_messages(
    section_nodes: Sequence[Node], section_path: str, doc_summary: str
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _USER_TEMPLATE.format(
            doc_summary=doc_summary or "(unknown document)",
            section_path=section_path or "(no section path)",
            window=render_window(section_nodes),
        )},
    ]


def prompt_hash() -> str:
    """Identity of the prompt PROGRAM, not of any one request.

    Depends on the version tag, both templates and the closed type list — so
    changing an instruction, or widening the enum, changes the hash and every
    edge stamped with the old one remains attributable to the instructions that
    actually produced it.
    """
    h = hashlib.sha256()
    for part in (PROMPT_VERSION, SYSTEM_PROMPT, _USER_TEMPLATE, *ALLOWED_EDGE_TYPES):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]
