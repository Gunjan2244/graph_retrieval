"""Domain packs.

A domain pack is CONFIG, not code paths. The engine core knows nothing about
law; it knows about closure edges and structural units. Everything
jurisdiction- or genre-specific lives in a pack, so adding medicine, technical
docs, or finance later means writing a new pack — never editing traversal.

Legal is the first pack because statutory text marks its own closure relations
lexically ("provided that", "notwithstanding", "subject to"), which means a
large share of the highest-value edges are obtainable with zero inference cost.
That property is unusual and is why law is the right wedge.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from re import Pattern

from dge.model import EdgeClass, EdgeType

# ---------------------------------------------------------------------------
# Pack framework
# ---------------------------------------------------------------------------


class Confidence(StrEnum):
    """How much a pattern hit should be trusted before model verification."""

    STRONG = "strong"      # lexically unambiguous; usable as an edge directly
    MEDIUM = "medium"      # usable as a candidate; verify before committing
    WEAK = "weak"          # a hint for the extractor's attention only


@dataclass(frozen=True, slots=True)
class MarkerPattern:
    """A lexical marker that suggests a typed relation."""

    name: str
    regex: Pattern[str]
    edge_type: EdgeType
    confidence: Confidence
    # Where the OTHER endpoint usually lives relative to the matched node.
    # 'preceding' -> the governed node is the previous sibling/parent clause.
    target_hint: str = "preceding"
    note: str = ""


class ClaimScope(StrEnum):
    """How far a provision's override claim reaches.

    Engine-level vocabulary — it is about graph reachability, not law — but the
    PATTERNS that recognise each scope are pack data (`override_scopes` below),
    because "notwithstanding anything contained in this Act" is a drafting
    convention, not a fact about graphs. `dge.l3.conflict` knows that two
    provisions each claiming to override the other is a conflict; it does not
    know a single word of English legal phrasing.
    """

    SECTION = "section"        # reaches one named provision
    DOCUMENT = "document"      # reaches everything in this document
    EXTERNAL = "external"      # reaches another instrument entirely
    UNRESOLVED = "unresolved"  # an override marker with no readable scope


@dataclass(frozen=True, slots=True)
class OverrideScopePattern:
    """Matched against the text immediately FOLLOWING an override marker.

    Order matters: the first pattern that matches wins, so put the narrow ones
    ("any other law for the time being in force") before the broad ones.
    """

    regex: Pattern[str]
    scope: ClaimScope


@dataclass(frozen=True, slots=True)
class StructuralUnit:
    """A genre-specific structural element with a fixed relation to its parent.

    These are free edges: no model call, no ambiguity. In statutory text they
    are also the highest-value closure edges in the corpus.
    """

    name: str
    regex: Pattern[str]
    edge_type: EdgeType
    edge_class: EdgeClass


@dataclass(frozen=True, slots=True)
class DomainPack:
    name: str
    extra_edge_types: Sequence[str]
    structural_units: Sequence[StructuralUnit]
    markers: Sequence[MarkerPattern]
    definition_patterns: Sequence[Pattern[str]]
    citation_patterns: Sequence[Pattern[str]]
    gate_terms: Sequence[str] = field(default_factory=tuple)
    override_scopes: Sequence[OverrideScopePattern] = field(default_factory=tuple)
    notes: str = ""

    def should_run_l3(self, text: str) -> bool:
        """Cost gate: run the expensive extractor only where a closure relation
        is plausible. In statutory corpora this typically skips a large fraction
        of sections outright."""
        low = text.lower()
        return any(t in low for t in self.gate_terms)


I = re.IGNORECASE


# ---------------------------------------------------------------------------
# Legal edge types
#
# These extend, not replace, dge.model.EdgeType. Register them in the policy
# table with a class; the traversal engine treats them by class alone.
# ---------------------------------------------------------------------------

LEGAL_EDGE_TYPES: dict[str, EdgeClass] = {
    # --- statutory closure ---
    "proviso_to":       EdgeClass.CLOSURE,   # carves an exception out of the main clause
    "explanation_to":   EdgeClass.CLOSURE,   # clarifies/extends meaning; binding
    "overrides":        EdgeClass.CLOSURE,   # non obstante clause
    "subject_to":       EdgeClass.CLOSURE,   # this yields to the referenced provision
    "deems":            EdgeClass.CLOSURE,   # deeming fiction alters the ordinary meaning
    "applies_only_to":  EdgeClass.CLOSURE,   # scope limitation
    "excepts":          EdgeClass.CLOSURE,   # "nothing in this section shall apply to"
    "repeals":          EdgeClass.CLOSURE,
    "saves":            EdgeClass.CLOSURE,   # savings clause preserves prior operation
    "substitutes":      EdgeClass.CLOSURE,   # amendment act text surgery
    "inserts":          EdgeClass.CLOSURE,
    "omits":            EdgeClass.CLOSURE,
    "commences_on":     EdgeClass.CLOSURE,   # temporal scope of operation
    # --- case law closure ---
    "overruled_by":     EdgeClass.CLOSURE,
    "reversed_by":      EdgeClass.CLOSURE,
    "per_incuriam":     EdgeClass.CLOSURE,   # decided in ignorance of binding authority
    "stayed_by":        EdgeClass.CLOSURE,
    "distinguished_by": EdgeClass.CLOSURE,   # narrows applicability — not merely context
    # --- case law context ---
    "affirmed_by":      EdgeClass.CONTEXT,
    "followed_by":      EdgeClass.CONTEXT,
    "relies_on":        EdgeClass.CONTEXT,
    "considers":        EdgeClass.CONTEXT,
    "obiter_in":        EdgeClass.CONTEXT,
    # --- contract closure ---
    "amended_by":       EdgeClass.CLOSURE,
    "waived_by":        EdgeClass.CLOSURE,
    "survives_termination": EdgeClass.CLOSURE,
    "capped_by":        EdgeClass.CLOSURE,   # liability caps qualify obligations
    "carve_out_of":     EdgeClass.CLOSURE,
    "governed_by":      EdgeClass.CONTEXT,
}


# ---------------------------------------------------------------------------
# Structural units — free edges, no model call
# ---------------------------------------------------------------------------

LEGAL_STRUCTURE: tuple[StructuralUnit, ...] = (
    StructuralUnit(
        "proviso",
        re.compile(r"^\s*provided\s+(?:further\s+|also\s+)?that\b", I),
        EdgeType.EXCEPTION_OF, EdgeClass.CLOSURE,
    ),
    StructuralUnit(
        "explanation",
        re.compile(r"^\s*Explanation\s*[\-—–.:]*\s*(?:[IVX0-9]+)?\s*[\-—–.:]", I),
        EdgeType.DEFINES, EdgeClass.CLOSURE,
    ),
    StructuralUnit(
        "illustration",
        re.compile(r"^\s*Illustrations?\s*[\-—–.:]", I),
        EdgeType.EXEMPLIFIES, EdgeClass.CONTEXT,
    ),
    StructuralUnit(
        "exception",
        re.compile(r"^\s*Exceptions?\s*(?:[IVX0-9]+)?\s*[\-—–.:]", I),
        EdgeType.EXCEPTION_OF, EdgeClass.CLOSURE,
    ),
    StructuralUnit(
        "sub_section",
        re.compile(r"^\s*\((\d+)\)\s+"),
        EdgeType.PART_OF, EdgeClass.CONTEXT,
    ),
    StructuralUnit(
        "clause",
        re.compile(r"^\s*\(([a-z]{1,2})\)\s+"),
        EdgeType.PART_OF, EdgeClass.CONTEXT,
    ),
    StructuralUnit(
        "schedule",
        re.compile(r"^\s*(?:THE\s+)?(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|[IVX]+)?\s*SCHEDULE\b", I),
        EdgeType.PART_OF, EdgeClass.CONTEXT,
    ),
)


# ---------------------------------------------------------------------------
# Markers — the money patterns
# ---------------------------------------------------------------------------

LEGAL_MARKERS: tuple[MarkerPattern, ...] = (
    # ---- non obstante: the strongest closure signal in Indian statutes ----
    MarkerPattern(
        "non_obstante",
        re.compile(
            r"notwithstanding\s+anything\s+"
            r"(?:contained\s+|to\s+the\s+contrary\s+)*(?:in|under)\b", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "referenced",
        note="Overrides the referenced provision. Resolve the reference to a node "
             "and write `overrides`. Two competing non obstante clauses is a real "
             "conflict — flag it, do not silently pick one. The qualifiers repeat "
             "in either order in real drafting: the original pattern required "
             "'contained' before 'to the contrary' and so missed 'notwithstanding "
             "anything TO THE CONTRARY CONTAINED in this Act' entirely — 170 -> 181 "
             "matches across the 62-act corpus once both orders are allowed "
             "(Phase 3, verified against Ajmer_Tenancy_and_Land_Records_Act,_1950, "
             "where the miss was suppressing real override conflicts).",
    ),
    MarkerPattern(
        "subject_to",
        re.compile(r"subject\s+to\s+the\s+(?:provisions\s+of|other\s+provisions)", I),
        EdgeType.CONDITIONED_ON, Confidence.STRONG, "referenced",
        note="Inverse of non obstante: this provision yields.",
    ),
    MarkerPattern(
        "save_as_provided",
        re.compile(r"(?:save|except)\s+as\s+(?:otherwise\s+)?(?:provided|expressly\s+provided)", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "nothing_shall_apply",
        re.compile(r"nothing\s+(?:contained\s+)?in\s+this\s+"
                   r"(?:section|Act|Chapter|sub-section|rule)\s+shall\s+"
                   r"(?:apply|extend|affect|be\s+deemed)", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG,
    ),
    MarkerPattern(
        "unless",
        re.compile(r"\bunless\s+(?:the\s+context|otherwise\s+(?:provided|required|expressly))", I),
        EdgeType.CONDITIONED_ON, Confidence.MEDIUM,
    ),
    MarkerPattern(
        "deeming",
        re.compile(r"shall\s+be\s+deemed\s+(?:to\s+(?:be|have)|not\s+to\s+be)", I),
        EdgeType.DEFINES, Confidence.STRONG,
        note="A deeming fiction displaces the ordinary meaning. Retrieving the "
             "ordinary term without the deeming provision is a wrong answer.",
    ),
    MarkerPattern(
        "shall_not_apply_to",
        re.compile(r"shall\s+not\s+apply\s+to\b", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG,
    ),
    MarkerPattern(
        "for_the_purposes_of",
        re.compile(r"for\s+the\s+purposes?\s+of\s+this\s+"
                   r"(?:section|Act|Chapter|clause|sub-section)", I),
        EdgeType.DEFINES, Confidence.MEDIUM, "following",
        note="Scoped definition. The scope is the named unit, NOT the whole Act — "
             "this is the shadowing case the symbol table exists for.",
    ),
    # ---- amendment surgery ----
    MarkerPattern(
        "substitution",
        re.compile(r"for\s+the\s+(?:words|figures|expression|brackets)[^,]{0,120},\s*"
                   r"the\s+(?:words|figures|expression)[^,]{0,120}\s+shall\s+be\s+substituted", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "insertion",
        re.compile(r"shall\s+be\s+inserted\b", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "omission",
        re.compile(r"shall\s+be\s+omitted\b", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "repeal",
        re.compile(r"\b(?:is|are|shall\s+stand)\s+hereby\s+repealed\b|"
                   r"\bRepeal\s+and\s+[Ss]avings?\b", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "referenced",
    ),
    # ---- case law treatment ----
    MarkerPattern(
        "overruled",
        re.compile(r"\b(?:is|are|stands?|hereby)\s+overruled\b|"
                   r"\bwe\s+overrule\b|\bno\s+longer\s+good\s+law\b", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "per_incuriam",
        re.compile(r"\bper\s+incuriam\b", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "referenced",
    ),
    MarkerPattern(
        "distinguished",
        re.compile(r"\b(?:is|are)\s+distinguishable\b|\bwe\s+distinguish\b|"
                   r"\bturned\s+on\s+its\s+own\s+facts\b", I),
        EdgeType.EXCEPTION_OF, Confidence.MEDIUM, "referenced",
        note="Narrows applicability — treat as closure, not context. A precedent "
             "cited without its distinguishing case is the classic wrong answer.",
    ),
    MarkerPattern(
        "affirmed",
        re.compile(r"\b(?:affirmed|upheld)\s+(?:in|by)\b", I),
        EdgeType.SUPPORTS, Confidence.MEDIUM, "referenced",
        note="'approved' was dropped from this alternation (Phase 0 hand-check, "
             "60-act sample): it fires on ordinary administrative language "
             "('scheme approved by the Central Government') that has nothing to "
             "do with judicial affirmation, which is what this marker exists to "
             "detect. 'affirmed'/'upheld' are judicial-specific enough to keep.",
    ),
    # ---- contract ----
    MarkerPattern(
        "notwithstanding_clause",
        re.compile(r"notwithstanding\s+(?:the\s+foregoing|anything\s+to\s+the\s+contrary)", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "preceding",
    ),
    MarkerPattern(
        "except_that",
        re.compile(r"\bexcept\s+(?:that|as\s+set\s+forth|to\s+the\s+extent)\b", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG, "preceding",
    ),
    MarkerPattern(
        "provided_however",
        re.compile(r"provided,?\s+however,?\s+that", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG, "preceding",
    ),
    MarkerPattern(
        "survival",
        re.compile(r"shall\s+survive\s+(?:any\s+)?(?:termination|expiry|expiration)", I),
        EdgeType.CONDITIONED_ON, Confidence.STRONG,
    ),
    MarkerPattern(
        "liability_cap",
        re.compile(r"(?:aggregate\s+liability|in\s+no\s+event\s+shall)[^.]{0,200}?"
                   r"(?:exceed|be\s+liable)", I),
        EdgeType.CONDITIONED_ON, Confidence.STRONG,
        note="Caps qualify every obligation in the agreement. High fan-out — but "
             "mandatory, so include it and let the degree penalty handle context "
             "edges elsewhere.",
    ),
)


# ---------------------------------------------------------------------------
# Definitions
#
# 'means' is exhaustive; 'includes' is illustrative and extends the ordinary
# meaning. Store which one it was — the distinction is litigated constantly and
# an engine that flattens it is giving wrong answers about scope.
# ---------------------------------------------------------------------------

LEGAL_DEFINITIONS: tuple[Pattern[str], ...] = (
    re.compile(r'[""«"]([^""»"]{2,80})[""»"]\s+means\b', I),
    re.compile(r'[""«"]([^""»"]{2,80})[""»"]\s+includes\b', I),
    re.compile(r'[""«"]([^""»"]{2,80})[""»"]\s+shall\s+(?:mean|have\s+the\s+meaning)', I),
    re.compile(r"\(\s*(?:herein(?:after)?\s+(?:referred\s+to\s+as|called)|the)\s*"
               r'[""«"]([^""»"]{2,80})[""»"]\s*\)', I),
    re.compile(r"^\s*\([a-z]\)\s+[\"“]([^\"”]{2,80})[\"”]\s+means", I | re.MULTILINE),
    re.compile(r"\bIn\s+this\s+(?:Act|Chapter|section|Agreement),\s+unless\s+the\s+context", I),
    re.compile(r'\bcapitalised\s+terms?[^.]{0,80}\bmeaning\s+(?:given|ascribed)\b', I),
)


# ---------------------------------------------------------------------------
# Citations — deterministic cross-document edges, free of charge
# ---------------------------------------------------------------------------

LEGAL_CITATIONS: tuple[Pattern[str], ...] = (
    # Indian reporters
    re.compile(r"\(?\b(19|20)\d{2}\)?\s*\(?\d{0,2}\)?\s*SCC\s+\d+", I),
    re.compile(r"\bAIR\s+(?:19|20)\d{2}\s+SC\s+\d+", I),
    re.compile(r"\b(19|20)\d{2}\s+INSC\s+\d+", I),               # neutral citation
    re.compile(r"\b(19|20)\d{2}\s+SCC\s+OnLine\s+\w+\s+\d+", I),
    re.compile(r"\bSCR\s+\d+|\bCriLJ\s+\d+|\bITR\s+\d+", I),
    # Statutory references
    re.compile(r"\b(?:[Ss]ection|[Ss]ec\.?|§)\s*(\d+[A-Z]{0,2})"
               r"(?:\s*\(\s*\d+\s*\))?(?:\s*\(\s*[a-z]{1,2}\s*\))?", ),
    re.compile(r"\b[Aa]rticle\s+(\d+[A-Z]{0,2})\b"),
    # `\s` not a literal space: PARSER_PLAN.md Decision 3 — `node.raw` can now
    # contain internal newlines from reflowed dialect-B text, so an Act name
    # wrapped mid-citation must still match.
    re.compile(r"\b(?:the\s+)?([A-Z][A-Za-z\s,]{4,60})\s+Act,?\s+((?:19|20)\d{2})\b"),
    re.compile(r"\b[Rr]ule\s+(\d+[A-Z]?)\b"),
    re.compile(r"\b[Oo]rder\s+([IVXL]+),?\s+[Rr]ule\s+(\d+)\b"),  # CPC style
    # Contract-internal
    re.compile(r"\b[Cc]lause\s+(\d+(?:\.\d+)*)\b"),
    re.compile(r"\b(?:Schedule|Annexure|Exhibit|Appendix)\s+([A-Z0-9]{1,3})\b"),
)


# ---------------------------------------------------------------------------
# Override scopes — how far a non obstante clause reaches
#
# Matched against the text immediately after a SUPERSEDES-typed marker, i.e.
# after "notwithstanding anything contained in". The scope is the whole game:
# "...in section 9" competes with one provision, "...in this Act" competes with
# every other act-wide claim in the document, and "...in any other law for the
# time being in force" competes with nothing inside this corpus at all.
#
# First match wins, so the narrow phrasings are listed first. Anything that
# matches none of these is ClaimScope.UNRESOLVED and is never flagged as a
# conflict — a repeal clause or an amendment marker also carries a SUPERSEDES
# edge type but has no override scope, and must not be mistaken for one.
# ---------------------------------------------------------------------------

LEGAL_OVERRIDE_SCOPES: tuple[OverrideScopePattern, ...] = (
    OverrideScopePattern(
        re.compile(r"\s*any\s+other\s+(?:law|enactment|Act)\b", I), ClaimScope.EXTERNAL,
    ),
    OverrideScopePattern(
        re.compile(r"\s*(?:the\s+)?[A-Z][A-Za-z\s,()]{4,60}\s+Act,?\s+(?:19|20)\d{2}\b"),
        ClaimScope.EXTERNAL,
    ),
    OverrideScopePattern(
        re.compile(r"\s*(?:the\s+provisions\s+of\s+)?(?:sub-)?section\s+\d+[A-Za-z]*", I),
        ClaimScope.SECTION,
    ),
    OverrideScopePattern(
        re.compile(r"\s*(?:the\s+(?:other\s+)?provisions\s+of\s+)?this\s+"
                   r"(?:Act|Chapter|Part|Agreement)\b", I),
        ClaimScope.DOCUMENT,
    ),
)


# Cost gate. Sections with none of these rarely carry closure relations.
LEGAL_GATE_TERMS: tuple[str, ...] = (
    "provided", "notwithstanding", "subject to", "except", "unless",
    "shall not apply", "deemed", "save as", "nothing in", "repeal",
    "substituted", "inserted", "omitted", "overruled", "distinguish",
    "survive", "in no event", "for the purposes of", "means", "includes",
)


LEGAL_PACK = DomainPack(
    name="legal",
    extra_edge_types=tuple(LEGAL_EDGE_TYPES),
    structural_units=LEGAL_STRUCTURE,
    markers=LEGAL_MARKERS,
    definition_patterns=LEGAL_DEFINITIONS,
    citation_patterns=LEGAL_CITATIONS,
    gate_terms=LEGAL_GATE_TERMS,
    override_scopes=LEGAL_OVERRIDE_SCOPES,
    notes="Covers Indian statutory text, judgments, and commercial contracts. "
          "See docs/07-legal-domain-pack.md for the semantics behind each edge type.",
)


REGISTRY: dict[str, DomainPack] = {"legal": LEGAL_PACK}


def get_pack(name: str) -> DomainPack:
    if name not in REGISTRY:
        raise KeyError(f"unknown domain pack {name!r}; registered: {sorted(REGISTRY)}")
    return REGISTRY[name]
