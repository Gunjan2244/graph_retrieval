"""Guards on the hard-case builder.

The builder writes the instrument that Phase 3's claim will be measured with,
so its guards matter more than most code: a question that echoes the gold span,
or a gold span that is not verbatim in the source, silently corrupts the
measurement rather than failing loudly. Each guard below is falsifiable --
revert the mechanism and one of these fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_hard_cases import MINED_TYPES, overlap_ratio

from dge.model import EdgeType


def test_a_question_copied_from_the_gold_span_is_rejected():
    """The failure mode LLM generation actually has. A question paraphrased
    from the gold tests lexical matching, not retrieval."""
    gold = ("Provided that where he attains the age of sixty-five years before "
            "the expiry of the said term of six years, he shall vacate office.")
    echoed = "When does he attain the age of sixty-five years and vacate office?"
    assert overlap_ratio(echoed, gold) > 0.5


def test_a_question_asked_about_the_rule_is_kept():
    """The shape the generator is supposed to produce: asks the practical
    question, shares little vocabulary with the carve-out."""
    gold = ("Provided that where he attains the age of sixty-five years before "
            "the expiry of the said term of six years, he shall vacate office.")
    natural = "How long is the Comptroller and Auditor-General appointed for?"
    assert overlap_ratio(natural, gold) <= 0.5


def test_stopwords_do_not_manufacture_overlap():
    """Legal prose is dense in shared function words. If those counted, every
    question would look like an echo and the guard would reject everything."""
    gold = "Provided that the person shall not be liable under this section."
    # Shares "under", "shall" and "this" with the gold and nothing else. Those
    # are function words; counting them would score a wholly unrelated question
    # as a partial echo, and the guard would start rejecting good questions.
    unrelated = "Under what conditions shall this inspection be permitted?"
    assert overlap_ratio(unrelated, gold) == 0.0


def test_an_empty_question_is_treated_as_maximum_overlap():
    """A degenerate generation must not slip through as 'zero overlap'."""
    assert overlap_ratio("", "anything at all here") == 1.0


def test_defines_is_not_mined():
    """`defines` is 2555 of 2829 closure edges; mining it would swamp the set
    and drown the exception and version cases the product claim rests on."""
    assert EdgeType.DEFINES not in MINED_TYPES


def test_the_mined_types_are_the_closure_relations_the_claim_rests_on():
    assert MINED_TYPES[EdgeType.EXCEPTION_OF] == "lost_exception"
    assert MINED_TYPES[EdgeType.SUPERSEDES] == "wrong_version"
    assert MINED_TYPES[EdgeType.CONDITIONED_ON] == "lost_scope"
