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
    # For target_hint='referenced' only: which side of THIS marker the citation
    # it governs sits on. Measured, not assumed — searching the whole node and
    # taking the first hit gave the wrong target on 8 of 54 corpus sites, e.g.
    # "...paid into court under section 98, such court shall, notwithstanding
    # anything in this Act..." resolved the non obstante clause to s.98, which
    # it does not mention. Non obstante / subject to / save as provided all end
    # their match at the preposition, so the citation FOLLOWS. The amendment
    # surgery markers are the inverse: "in section 1, ... shall be substituted"
    # names its target BEFORE the operative phrase. 'within' is for markers
    # whose match spans the citation, so that the evidence span is the claim
    # itself rather than the two words that introduce it.
    ref_side: str = "after"
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
    # How a `referenced` marker's citation is read. Both are pack data because
    # both are drafting conventions, not facts about graphs (invariant 11) —
    # `dge.edges` previously hardcoded `section\s+(\d+)`, which is a sentence
    # of English legal usage living in the engine.
    #
    # `section_ref_pattern` must expose the number list in group 1;
    # `foreign_ref_pattern` is matched against the text IMMEDIATELY FOLLOWING
    # that list, and a match means the citation belongs to another instrument
    # and therefore has no target in this document.
    #
    # A pack that leaves these unset resolves no `referenced` markers at all,
    # which is the correct default: silently guessing a target is how you get a
    # confident wrong CLOSURE edge, and closure edges are unbudgeted.
    section_ref_pattern: Pattern[str] | None = None
    foreign_ref_pattern: Pattern[str] | None = None
    # Sub-section citations ("sub-section (1)", "sub-sections (1) and (2)",
    # "sub-section (1) of section 12") are a DIFFERENT population from
    # `section_ref_pattern` and unresolvable by it: the parenthesised
    # enumerator has no bare digit for `section_ref_pattern` to match, and
    # even if it did, `_build_cursor`'s `section_registry` has no entry for a
    # sub-section — only for headings. Group 1 is the enumerator list (same
    # multi-value convention as `section_ref_pattern`); group 2, if present,
    # is the enclosing section's number when the citation crosses sections
    # ("... of section 12") rather than staying local to the citing node's own
    # section. A pack that leaves this unset resolves no sub-section
    # citations — same silence-over-guess default as above.
    subsection_ref_pattern: Pattern[str] | None = None
    # Punctuation that ends the clause a marker is in. Anything after it
    # belongs to a different clause and is not what the marker governs.
    clause_break_pattern: Pattern[str] | None = None
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
        "nothing_in_referenced_shall_apply",
        # The cross-reference carve-out — and the form the corpus measurement
        # said was the leaky one. `nothing_shall_apply` below handles
        # "nothing in THIS section", which points at itself and resolves
        # structurally; this handles "nothing in section 28 shall apply",
        # which points somewhere else and had no marker at all. Two of the
        # labelled `lost_exception` cases turn on it, including Mines Act 1952
        # s.37 and the Child Labour Act's family-establishment carve-out.
        #
        # The match deliberately SPANS the citation (`ref_side="within"`) and
        # stops just before the verb, so `evidence_span` reads "Nothing in
        # Secs . 7, 8 and 9" — the claim — instead of "Nothing in".
        #
        # The verb list is from the corpus, not from imagination: of the four
        # sites where a citation follows, the verbs are apply (2), be (1) and
        # authorise (1). `(?!\s+this\b)` keeps this disjoint from the marker
        # below, so a node never gets both.
        re.compile(
            r"nothing\s+(?:contained\s+)?in\b(?!\s+this\b)[^;]{0,60}?"
            r"(?=\s+shall\s+(?:apply|be|affect|extend|authoris|authoriz|render|"
            r"entitle|prevent|operate)\w*\b)", I),
        EdgeType.EXCEPTION_OF, Confidence.STRONG, "referenced", "within",
        note="Disapplies the named provisions. Fires only where a citation is "
             "actually present; 'nothing in the foregoing provisions' matches "
             "but resolves to no target, which is the correct outcome.",
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
        note="Scoped definition, SELF-referential variant. The scope is the named "
             "unit, NOT the whole Act — this is the shadowing case the symbol "
             "table exists for. Disjoint from "
             "`for_the_purposes_of_referenced` below by the literal 'this'.",
    ),
    MarkerPattern(
        "for_the_purposes_of_referenced",
        # Same drafting formula, CITED variant: "For the purposes of clause (ii)
        # of sub-section (1), the expenditure ... shall mean X" scopes a
        # definition to a provision it NAMES, where the marker above scopes to
        # the one it sits in. The two need different targets, so they are two
        # markers rather than one with a guessed hint — the same split
        # `ref_side` already makes for non obstante vs amendment surgery.
        #
        # Measured over the 62-act corpus: 23 sites where a citation is the
        # direct object of "for the purposes of", against 119 self-referential
        # "this <unit>" sites. The old marker matched neither of the 23 (it
        # requires the literal "this"), so `target_hint="following"` was never
        # wrong on them — they were simply invisible, the same shape of gap as
        # sub-section citations.
        #
        # The lookahead is what keeps this disjoint from ordinary prose:
        # "for the purposes of enrolment" (169 such sites) must not fire. The
        # match itself deliberately stops BEFORE the citation, so `ref_side`
        # stays "after" and `foreign_ref_pattern` can still see the trailing
        # "of the Indian Penal Code" — several of the 23 cite other
        # instruments, and a "within" match would hide that from the guard and
        # fabricate a local target.
        re.compile(
            r"for\s+the\s+purposes?\s+of\s+"
            # Lookahead only — nothing after "of " is consumed, so `ref_side`
            # stays "after", the citation scan starts at the citation, and
            # `foreign_ref_pattern` can still see a trailing "of the Indian
            # Penal Code". A consuming match would hide that and fabricate a
            # local target.
            r"(?=(?:(?:clause|item|paragraph|sub-?\s*clause)\s*\([^)]{1,6}\)\s*of\s+)?"
            r"(?:sub-?\s*sections?\s*(?:\(\s*\d{1,3}[A-Za-z]?\s*\)\s*"
            r"(?:,\s*|(?:,\s*)?(?:and|or|&|to)\s*)?)+"
            r"|sections?\s*\.?\s*\d{1,4}[A-Z]{0,2}"
            r"(?:\s*(?:,|and|&|or)\s*\d{1,4}[A-Z]{0,2})*)"
            r"(?:\s*and\s+this\s+\w+)?"
            # The clause boundary is the whole discriminator — see the note.
            r"\s*[,\u2013\u2014-])", I),
        EdgeType.DEFINES, Confidence.STRONG, "referenced", "after",
        note="Scopes a definition to the provision it CITES. Orientation is "
             "`EdgeType.DEFINES`'s: the cited provision is `src` (the usage) "
             "and this node is `dst` (the meaning), so traversal walks FORWARD "
             "from a provision to the definition that governs it. "
             "The trailing clause boundary in the lookahead is load-bearing, "
             "not defensive padding: 'for the purposes of X' scopes a "
             "definition only when the citation prefaces a rule "
             "('for the purposes of sub-section (2), it shall be presumed'), "
             "and is merely referential when it runs on into a verb or noun "
             "phrase ('any authority prescribed for the purposes of "
             "sub-section (1) may'). Hand-labelling the corpus population "
             "separated 10/10 scoping uses from 4/4 referential ones on that "
             "rule alone. It is a claim about English syntax that can be "
             "stated and tested, not a constant fitted to a sample — the same "
             "discipline as `clause_break_pattern`.",
    ),
    # ---- amendment surgery ----
    MarkerPattern(
        "substitution",
        re.compile(r"for\s+the\s+(?:words|figures|expression|brackets)[^,]{0,120},\s*"
                   r"the\s+(?:words|figures|expression)[^,]{0,120}\s+shall\s+be\s+substituted", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced", "before",
    ),
    MarkerPattern(
        "insertion",
        re.compile(r"shall\s+be\s+inserted\b", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced", "before",
    ),
    MarkerPattern(
        "omission",
        re.compile(r"shall\s+be\s+omitted\b", I),
        EdgeType.AMENDS, Confidence.STRONG, "referenced", "before",
    ),
    MarkerPattern(
        "repeal",
        re.compile(r"\b(?:is|are|shall\s+stand)\s+hereby\s+repealed\b|"
                   r"\bRepeal\s+and\s+[Ss]avings?\b", I),
        EdgeType.SUPERSEDES, Confidence.STRONG, "referenced", "before",
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

# ---------------------------------------------------------------------------
# Reference resolution — how a `referenced` marker finds its target
#
# Both patterns are pack data, not engine code, because both encode English
# legal drafting convention (invariant 11). `dge.edges` used to carry
# `re.compile(r"section\s+(\d+[A-Za-z]*)")` and take the FIRST hit in the whole
# node, which was wrong twice over on the real corpus: it missed every plural
# form and it fabricated targets. Measured over 63 Central Acts, 14 of the 54
# resolved sites were wrong (26%) — and every one of them was a CLOSURE-class
# edge, the kind traversal follows unbudgeted and mandatorily.
# ---------------------------------------------------------------------------

# Group 1 is the NUMBER LIST, which may name several provisions at once:
# "sections 57 and 58", "Secs . 7, 8 and 9", "section 21, section 22 or
# section 23". The list form is not a curiosity — "Nothing in section 28,
# section 30, section 31, section 34 ... shall apply to persons employed in a
# supervising capacity" (Mines Act 1952 s.37) is verbatim one of the labelled
# failure cases, and under a single-target resolver four of its five citations
# were silently dropped.
#
# `\s*\.?\s*` after the lead-in is not defensive padding: India Code's text
# really does contain "Secs . 7", with the space before the period.
LEGAL_SECTION_REF: Pattern[str] = re.compile(
    r"\b(?:sections?|secs?|ss)\s*\.?\s*"
    r"(\d{1,4}[A-Z]{0,2}(?:\s*(?:,|and|&|or)\s*\d{1,4}[A-Z]{0,2})*)",
    I,
)

# Matched against the text IMMEDIATELY AFTER the number list. A hit means the
# citation names another instrument, so it has NO target in this document and
# must resolve to nothing.
#
# This is the fabrication guard, and it is doing real work: "Notwithstanding
# anything contained in section 12 of the Central Goods and Services Tax Act,
# no tax shall be payable..." was resolving to *this* Act's section 12 — a
# STRONG-confidence `supersedes` edge to a provision the sentence never
# mentions. 6 of 54 sites.
#
# `[A-Z]` for the instrument's first word is what separates a foreign citation
# from a local one: "of this Act" and "of that sub-section" stay lowercase and
# are correctly left alone. `said|principal|repealed` are spelled out because
# amendment acts refer to their target as "the said Act" in lower case, and
# that Act is emphatically not this one.
LEGAL_FOREIGN_REF: Pattern[str] = re.compile(
    r"\A\s*of\s+(?:the\s+)?(?:said|principal|repealed|[A-Z][\w'\u2019-]*)"
    r"(?:[\s,][\w'\u2019.,()\[\]-]+){0,10}?\s*"
    r"(?:Act|Code|Ordinance|Constitution|Regulations?|Rules)\b",
    I,
)


# ---------------------------------------------------------------------------
# Sub-section references — a population `LEGAL_SECTION_REF` cannot see
#
# `LEGAL_SECTION_REF` matches "section" (or "sec"/"ss") followed by a BARE
# number. "sub-section (1)" has no bare number for it to find — the digit is
# inside parentheses — so the citation is not mis-resolved, it is never
# recognised at all. Measured over the 62-act corpus: 1111 bare sub-section
# citations, 310 in "sub-section (N) of section M" form, 47 as an explicit
# list ("sub-sections (1) and (2)"), plus 2 of the "sub- section" (hyphen,
# space) variant India Code's text actually contains. All four shapes are
# below in `tests/test_edges.py`.
#
# Group 1 is the raw parenthesised list, split into enumerators by
# `_SUBSECTION_LIST_ENUM_RE` in `dge.edges` rather than here, so the pack
# stays declarative and the splitting logic lives with the resolver that
# consumes it. Group 2, when present, is the citation's OWN enclosing
# section — "of section 12" — which changes where the enumerator is looked
# up: within the citing node's own section by default, or within the named
# section when this group matches. Getting that scope wrong is not a smaller
# version of the fabrication bug fixed above — it is the SAME bug: "(1)" is
# repeated in nearly every section of every Act, so resolving it without the
# correct section as scope links a citation to an arbitrary, unrelated
# provision.
# The separator alternation accepts "to" as well as ",", "and", "or", "&" —
# "sub-sections (2) to (4)" is a RANGE, not a two-item list, and appears 6
# times in the 62-act corpus. `dge.edges._expand_subsection_enumerators`
# does the actual range expansion (2 -> 2,3,4); this pattern only has to
# admit "to" as a valid continuation so group 1 does not stop at "(2)" and
# silently drop "(4)" the way the section-level resolver's list splitter
# would if "to" were absent from its separator set.
LEGAL_SUBSECTION_REF: Pattern[str] = re.compile(
    r"\bsub-?\s*sections?\s*"
    r"((?:\(\s*\d{1,3}[A-Za-z]?\s*\)\s*(?:,\s*|(?:,\s*)?(?:and|or|&|to)\s*)?)+)"
    r"(?:\s*of\s+section\s*\.?\s*(\d{1,4}[A-Z]{0,2}))?",
    I,
)


# Between an `after`-side marker and the citation it governs there may be a
# path expression ("the first proviso to", "clause (e) of sub-section (3) of")
# but never a new clause. This is the whole discriminator, and it fell out of
# the data rather than being tuned to it: across the corpus's 39 `after`-side
# sites, every citation the marker actually governs has NO comma before it, and
# every citation belonging to a different clause has one. Sorted by distance the
# two groups interleave — a gap threshold would have to be fitted — but sorted
# by "is there a clause break", they separate exactly.
#
#   governed    ' ', ' in ', ' the first proviso to ',
#               ' in clause (e) of sub-section (3) of '
#   NOT governed ' the Code, where an order under ',
#               ' of this Act, the directors appointed under '
#
# The failure it prevents: "Notwithstanding anything contained in THIS ACT, the
# record ... shall be submitted for confirmation in accordance with the
# provisions of section 183" was writing `supersedes` against s.183, 147
# characters and two clauses away. The clause the marker qualifies is "this
# Act", which names no section — so the honest answer is no edge.
LEGAL_CLAUSE_BREAK: Pattern[str] = re.compile(r"[,;:.]")


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
    section_ref_pattern=LEGAL_SECTION_REF,
    foreign_ref_pattern=LEGAL_FOREIGN_REF,
    subsection_ref_pattern=LEGAL_SUBSECTION_REF,
    clause_break_pattern=LEGAL_CLAUSE_BREAK,
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
