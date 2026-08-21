"""Adversarial tests for CLAUDE.md invariant 10's enforcement point.

Written before any model call existed, because every model-extracted edge in
the system is only as trustworthy as this check. The cases below are the ways a
model actually fails: it paraphrases, it tidies whitespace, it changes case, it
quotes something it read in a different section, or it cites a fragment so
short it matches anything.
"""

from __future__ import annotations

from dge.l3.evidence import (
    MIN_EVIDENCE_CHARS,
    EvidenceVerdict,
    check_evidence,
)

# A window shaped like what the extractor actually sees: hard-wrapped lines, a
# non-breaking space, an em dash — i.e. real Indian bare-act text after
# PARSER_PLAN.md's reflow, not a tidy one-liner. The \u00a0 is written as an
# escape on purpose: it is load-bearing for two tests below and invisible if
# pasted literally, which is precisely how HANDOFF.md says gold spans silently
# break.
WINDOW = (
    "[N1] 12. Limitation on transfer.-\n"
    "[N2] (1) No person shall transfer any specified asset\n"
    "except with the previous approval of the Board.\n"
    "[N3] Provided that nothing in this section shall apply to a\u00a0transfer "
    "made in the ordinary course of business."
)


def test_the_fixture_really_carries_the_pathologies_it_claims_to():
    # Guards the tests below: if someone retypes WINDOW and loses the NBSP or
    # the hard wrap, the reflow tests would still pass while testing nothing.
    assert "\u00a0" in WINDOW
    assert "asset\nexcept" in WINDOW


def test_exact_substring_is_accepted_verbatim():
    check = check_evidence("No person shall transfer any specified asset", WINDOW)
    assert check.verdict is EvidenceVerdict.EXACT
    assert check.span == "No person shall transfer any specified asset"
    assert WINDOW[check.start:check.end] == check.span


def test_paraphrase_is_rejected_even_when_it_is_a_faithful_summary():
    # The single most important case: a TRUE statement about the window that is
    # not IN the window. This is what a confident hallucinated edge looks like.
    check = check_evidence(
        "transfers of specified assets require Board approval", WINDOW
    )
    assert check.verdict is EvidenceVerdict.REJECTED
    assert check.reason == "not_in_window"
    assert check.span is None


def test_near_miss_single_word_substitution_is_rejected():
    # "any" -> "a": one word off, still not verbatim.
    check = check_evidence("No person shall transfer a specified asset", WINDOW)
    assert check.verdict is EvidenceVerdict.REJECTED


def test_span_from_a_different_section_is_rejected_with_no_special_rule():
    # One section per call is what makes this free: text from elsewhere in the
    # document was never in the window, so it fails the same check as a
    # fabrication.
    check = check_evidence(
        "Every company shall maintain a register of members", WINDOW
    )
    assert check.verdict is EvidenceVerdict.REJECTED
    assert check.reason == "not_in_window"


def test_whitespace_normalized_span_is_accepted_but_stored_verbatim():
    # The model collapsed the hard wrap to a single space, as every model does.
    # Accepted — but what comes back is the WINDOW's text, newline included,
    # not the model's rendering of it.
    model_said = "No person shall transfer any specified asset except with the previous approval"
    check = check_evidence(model_said, WINDOW)
    assert check.verdict is EvidenceVerdict.REFLOWED
    assert check.span != model_said, "the model's whitespace must not be what gets stored"
    assert "\n" in check.span
    assert WINDOW[check.start:check.end] == check.span, "stored span must slice the window"


def test_non_breaking_space_is_treated_as_whitespace_for_matching():
    # The corpus is full of U+00A0. A model that types an ordinary space where
    # the source has one is not fabricating anything — but the span that gets
    # STORED must still carry the window's real character.
    check = check_evidence("shall apply to a transfer made in the ordinary course", WINDOW)
    assert check.verdict is EvidenceVerdict.REFLOWED
    assert "\u00a0" in check.span, "recovered span must carry the window's real characters"
    assert check.span in WINDOW


def test_reflow_tolerance_can_be_switched_off_for_a_strict_caller():
    model_said = "No person shall transfer any specified asset except with the previous approval"
    assert check_evidence(model_said, WINDOW, allow_reflow=False).verdict is (
        EvidenceVerdict.REJECTED
    )


def test_case_change_is_rejected_and_reported_distinctly():
    check = check_evidence("NO PERSON SHALL TRANSFER ANY SPECIFIED ASSET", WINDOW)
    assert check.verdict is EvidenceVerdict.REJECTED
    assert check.reason == "case_mismatch_only"


def test_too_short_a_span_is_rejected_even_when_present_verbatim():
    # "transfer" IS in the window. It is also in every other section of every
    # act ever drafted, so it identifies nothing.
    check = check_evidence("transfer", WINDOW)
    assert check.verdict is EvidenceVerdict.REJECTED
    assert check.reason.startswith("too_short")


def test_min_chars_floor_is_the_documented_constant():
    exactly_at_floor = "shall apply"  # 11 chars
    assert len(exactly_at_floor) == MIN_EVIDENCE_CHARS - 1
    assert check_evidence(exactly_at_floor, WINDOW).verdict is EvidenceVerdict.REJECTED


def test_missing_and_empty_spans_are_rejected_never_waved_through():
    assert check_evidence(None, WINDOW).reason == "missing"
    assert check_evidence("", WINDOW).reason == "empty"
    assert check_evidence("   \n\t ", WINDOW).reason == "empty"


def test_padding_a_short_span_with_whitespace_does_not_beat_the_floor():
    # The floor is measured on collapsed content, not raw length.
    check = check_evidence("   transfer   \n\n  ", WINDOW)
    assert check.verdict is EvidenceVerdict.REJECTED
    assert check.reason.startswith("too_short")


def test_leading_and_trailing_whitespace_on_a_real_span_is_forgiven():
    check = check_evidence("  \n No person shall transfer any specified asset \n ", WINDOW)
    assert check.verdict is EvidenceVerdict.EXACT
    assert check.span == "No person shall transfer any specified asset"


def test_recovered_span_always_slices_the_window_for_every_accepted_case():
    # The invariant that makes REFLOWED safe: whatever comes back is window
    # text. If this ever fails, `edges.evidence_span` has stopped being
    # verifiable against the substrate and invariant 10 is enforced by nothing.
    for candidate in [
        "No person shall transfer any specified asset",
        "No person shall transfer any specified asset except with the previous approval",
        "shall apply to a transfer made in the ordinary course",
        "Limitation on transfer.-",
    ]:
        check = check_evidence(candidate, WINDOW)
        assert check.ok, candidate
        assert WINDOW[check.start:check.end] == check.span
        assert check.span in WINDOW
