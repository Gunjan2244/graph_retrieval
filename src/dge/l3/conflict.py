"""Competing non obstante clauses — flagged, never resolved.

docs/07 7.2: "Two competing non obstante clauses is a real, litigated conflict.
Flag it; never silently pick one. Surfacing 'these two provisions each claim to
override the other' is more valuable than any answer you could synthesise."

HOW A CONFLICT IS REPRESENTED IN THE GRAPH — the design decision
================================================================

**A conflict is not an edge type and not a node. It is a derived finding over
the closure subgraph, plus — where both claims resolve — the two `supersedes`
edges that were already there.**

Concretely, two cases:

1. *Both claims name a provision.* Section 12 says "notwithstanding anything
   contained in section 9…" and section 9 says "notwithstanding anything
   contained in section 12…". L3a has already written both edges. The conflict
   IS the 2-cycle they form. Nothing new is stored; `detect_override_conflicts`
   reports it by reading the graph back.

2. *A claim is document-scoped* ("notwithstanding anything contained in this
   Act"). It resolves to no single node, so there is no edge to write — an
   edge to every node in the act is a hub, not information — and the finding
   over the claim set is the whole representation.

Why not the obvious alternatives:

- **A `conflicts_with` edge type.** It would be symmetric, so it would assert
  exactly what the two `supersedes` edges already assert, and it would need its
  own entry in `dge.traversal.policy` to be traversable — the pack-boundary
  leak CLAUDE.md invariant 11 names explicitly. Storing a second copy of a fact
  the graph already carries also means the two can disagree.
- **A synthesised "conflict node".** Every node's `raw` is a byte span of a
  real document (invariant 1). A conflict node's text exists in no document, so
  it would carry either fabricated `raw` or text copied from both provisions —
  invariant 4. docs/03 3.3 does say a searchable relation should be promoted to
  a node, but that applies to relations a document actually states; this one is
  our inference about two documents' interaction.
- **An `overrides` edge with a winner.** That is the one thing docs/07 forbids
  outright.
- **A `resolved` / `winner` column.** Same objection, plus it would be an
  ingest-time judgement baked into storage, where invariant 5 says filter at
  traversal time.

What traversal does and does not guarantee here — MEASURED, not assumed
-----------------------------------------------------------------------
The tempting claim is "reporting is enough, because `supersedes` is mandatory
closure on the reverse index, so retrieving either provision necessarily pulls
in the other." That is **half true on the real substrate**, and the half that
is false is worth knowing before relying on it.

A `referenced` marker resolves to the *section heading* of the section it
names (`dge.edges._resolve_target` against the section registry), while the
clause making the claim is a sub-section node. So the edges in a mutual
override are:

    s9(1)  --supersedes-->  heading of s.12
    s12(1) --supersedes-->  heading of s.9

which is a 2-cycle between the two SECTIONS, not between the two clauses.
Consequences, both verified in `tests/test_conflict.py`:

  - Seeding on a section heading DOES pull the competing provision in.
    `incoming(heading of s.9)` finds `s12(1)` on the reverse index, closure is
    mandatory and unbudgeted, and the competitor arrives. The guarantee holds.
  - Seeding ONLY on the bare sub-section does NOT. Nothing points at `s9(1)`
    itself; the hop from a clause to its own heading is `part_of`, which is a
    budgeted CONTEXT edge, so the path to the competitor runs through the
    cuttable half of the traversal.

So the finding this module produces is not merely a name for something
traversal already guarantees — for clause-level seeds it is the only thing
that surfaces the conflict, which is why `dge.query` reports it alongside the
soundness verdict rather than leaving it to callers.

The underlying question — whether a closure relation asserted against a
section should propagate to that section's children — is a real traversal
design decision that is NOT settled here. Making `part_of` closure-traversable
would unify the budgeted and unbudgeted halves behind one mechanism, which
CLAUDE.md invariant 6 explicitly forbids doing casually. It is recorded as an
open item in BUILD_PLAN.md Phase 3 instead of being decided as a side effect
of this module.

Confidence is not decoration here
---------------------------------
`MUTUAL_REFERENCE` is 1.0: each provision names the other's section, which is
unambiguous on the face of the text. `DOCUMENT_SCOPE_CLUSTER` is 0.5: two
act-wide non obstante clauses genuinely *claim* to override each other, but
whether they actually collide depends on whether their subject matter overlaps
— a judgement no lexical rule can make and one this module deliberately does
not attempt. Half confidence, surfaced, never resolved.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from dge.domains.legal import ClaimScope, DomainPack
from dge.l3.sections import Section, group_sections
from dge.model import EdgeType, Node, NodeKind

# Section identity, from either heading dialect, seeing through India Code's
# amendment bracket — the same shape `dge.edges._HEADING_KEY_RE` uses, kept
# here rather than imported so this module depends only on the pack and the
# substrate.
_HEADING_KEY_RE = re.compile(
    r"^\s*(?:(?:Section|Chapter|Article)\s+)?(?:\d{1,3}\[)*(\d{1,4}[A-Z]{0,2})\.",
    re.IGNORECASE,
)
_SECTION_REF_RE = re.compile(r"(?:sub-)?section\s+(\d+[A-Za-z]*)", re.IGNORECASE)


class ConflictKind(StrEnum):
    MUTUAL_REFERENCE = "mutual_reference"
    DOCUMENT_SCOPE_CLUSTER = "document_scope_cluster"


@dataclass(frozen=True, slots=True)
class OverrideClaim:
    """One provision asserting priority over something."""

    node_id: str
    doc_id: str
    section: str | None          # the section this claim was made IN
    scope: ClaimScope
    target_section: str | None   # the section this claim reaches, if named
    evidence_span: str           # verbatim, by construction: a regex match slice
    marker: str


@dataclass(frozen=True, slots=True)
class OverrideConflict:
    kind: ConflictKind
    doc_id: str
    claims: tuple[OverrideClaim, ...]
    confidence: float

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(c.node_id for c in self.claims)

    def describe(self) -> str:
        where = ", ".join(
            f"s.{c.section}" if c.section else c.node_id for c in self.claims
        )
        if self.kind is ConflictKind.MUTUAL_REFERENCE:
            return (
                f"{where} each expressly override the other "
                f"(confidence {self.confidence:.2f}) — unresolved by design"
            )
        return (
            f"{len(self.claims)} provisions ({where}) each claim priority over the "
            f"whole document (confidence {self.confidence:.2f}); whether they collide "
            f"depends on subject-matter overlap, which is not decided here"
        )


def _section_number(section: Section) -> str | None:
    for node in section.nodes:
        if node.kind is NodeKind.STRUCTURAL:
            m = _HEADING_KEY_RE.search(node.raw)
            if m:
                return m.group(1).upper()
    return None


def _classify_scope(
    pack: DomainPack, remainder: str
) -> tuple[ClaimScope, str | None]:
    for pattern in pack.override_scopes:
        m = pattern.regex.match(remainder)
        if not m:
            continue
        target = None
        if pattern.scope is ClaimScope.SECTION:
            ref = _SECTION_REF_RE.search(m.group(0))
            target = ref.group(1).upper() if ref else None
        return pattern.scope, target
    return ClaimScope.UNRESOLVED, None


def override_claims(nodes: Sequence[Node], pack: DomainPack) -> list[OverrideClaim]:
    """Every override claim in `nodes`, with the scope it reaches.

    An override claim is a hit on a marker the PACK typed as `SUPERSEDES`
    whose following text matches one of the pack's `override_scopes`. Both
    halves are required: `repeal` and `substitution` are also SUPERSEDES-typed
    markers, but neither is followed by an override scope, so neither is
    mistaken for a non obstante clause. The engine never names a marker.
    """
    if not pack.override_scopes:
        return []

    claims: list[OverrideClaim] = []
    for section in group_sections(nodes):
        number = _section_number(section)
        for node in section.nodes:
            if node.kind is not NodeKind.PROPOSITION:
                continue
            for marker in pack.markers:
                if marker.edge_type is not EdgeType.SUPERSEDES:
                    continue
                m = marker.regex.search(node.raw)
                if not m:
                    continue
                scope, target = _classify_scope(pack, node.raw[m.end():])
                if scope is ClaimScope.UNRESOLVED:
                    continue
                claims.append(OverrideClaim(
                    node_id=node.node_id,
                    doc_id=node.doc_id,
                    section=number,
                    scope=scope,
                    target_section=target,
                    evidence_span=m.group(0),
                    marker=marker.name,
                ))
    return claims


def _covers(claim: OverrideClaim, other: OverrideClaim) -> bool:
    """Does `claim` assert priority over the provision `other` sits in?"""
    if claim.doc_id != other.doc_id or claim.node_id == other.node_id:
        return False
    if claim.scope is ClaimScope.DOCUMENT:
        # An act-wide claim reaches every other provision of the act, but
        # saying a provision overrides its own section is noise, not conflict.
        return claim.section != other.section or claim.section is None
    if claim.scope is ClaimScope.SECTION:
        return claim.target_section is not None and claim.target_section == other.section
    return False  # EXTERNAL claims reach outside this corpus


def detect_override_conflicts(
    nodes: Sequence[Node], pack: DomainPack
) -> list[OverrideConflict]:
    """Provisions that each claim to override the other.

    Pure and derived: same corpus in, same findings out, regardless of ingest
    order (invariant 8). Nothing here mutates the graph.
    """
    claims = override_claims(nodes, pack)
    conflicts: list[OverrideConflict] = []

    # Case 1 — at least one side names the other's section. Reported pairwise
    # because each pair is a specific, litigable collision.
    seen: set[tuple[str, str]] = set()
    for a in claims:
        for b in claims:
            if a.node_id >= b.node_id:
                continue
            if a.scope is ClaimScope.DOCUMENT and b.scope is ClaimScope.DOCUMENT:
                continue  # handled as a cluster below, or it explodes quadratically
            if not (_covers(a, b) and _covers(b, a)):
                continue
            key = (a.node_id, b.node_id)
            if key in seen:
                continue
            seen.add(key)
            conflicts.append(OverrideConflict(
                ConflictKind.MUTUAL_REFERENCE, a.doc_id, (a, b), confidence=1.0,
            ))

    # Case 2 — act-wide claims. Grouped per document into ONE finding: N such
    # clauses in an act would otherwise produce N*(N-1)/2 near-identical pair
    # findings, which buries the specific conflicts from case 1.
    by_doc: dict[str, list[OverrideClaim]] = {}
    for claim in claims:
        if claim.scope is ClaimScope.DOCUMENT:
            by_doc.setdefault(claim.doc_id, []).append(claim)
    for doc_id, doc_claims in by_doc.items():
        distinct_sections = {c.section for c in doc_claims}
        if len(doc_claims) < 2 or len(distinct_sections) < 2:
            continue
        conflicts.append(OverrideConflict(
            ConflictKind.DOCUMENT_SCOPE_CLUSTER, doc_id, tuple(doc_claims), confidence=0.5,
        ))
    return conflicts
