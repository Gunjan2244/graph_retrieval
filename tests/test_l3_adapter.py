"""The LiteLLM adapter's parse/mapping layer, against recorded responses.

No key, no network: `_complete` is replaced with a recorded payload, exactly
the way `tests/test_vectors.py` stands in for a real embedder. This pins the
half of the adapter that is our code — label resolution, fence stripping,
schema validation — and deliberately says nothing about whether a real model
returns good edges.
"""

from __future__ import annotations

import pytest

from dge.adapters.extract_llm import ExtractorError, LiteLLMEdgeExtractor
from dge.l3.prompt import prompt_hash, ref_labels
from dge.l3.schema import ExtractionResponse, response_json_schema
from dge.parsing import PlainTextParser, finalize_doc_id

pytest.importorskip("pydantic")

DOC = (
    b"Section 12. Limitation on transfer.\n\n"
    b"(1) No person shall transfer any specified asset.\n\n"
    b"Provided that nothing in this section shall apply to a transfer by operation of law.\n"
)


@pytest.fixture
def nodes():
    result = PlainTextParser().parse(DOC)
    parsed, _edges = finalize_doc_id("d", list(result.nodes), list(result.structural_edges))
    return parsed


def _recorded(extractor: LiteLLMEdgeExtractor, payload: str) -> None:
    extractor._complete = lambda messages, labels: payload  # type: ignore[method-assign]


def test_labels_resolve_to_real_node_ids(nodes):
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")
    _recorded(extractor, """
        {"edges": [{"src_ref": "N3", "dst_ref": "N2", "type": "exception_of",
                    "evidence_span": "Provided that nothing in this section shall apply",
                    "confidence": 0.9}]}
    """)
    candidates = extractor.extract(nodes, "Section 12", "test doc")
    labels = ref_labels(nodes)
    assert len(candidates) == 1
    assert candidates[0].src == labels["N3"]
    assert candidates[0].dst == labels["N2"]
    assert candidates[0].type == "exception_of"


def test_a_label_that_does_not_exist_is_dropped_not_passed_on_as_a_node_id(nodes):
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")
    _recorded(extractor, """
        {"edges": [{"src_ref": "N99", "dst_ref": "N2", "type": "exception_of",
                    "evidence_span": "Provided that nothing in this section",
                    "confidence": 0.9}]}
    """)
    assert extractor.extract(nodes, "Section 12", "test doc") == []


def test_fenced_json_is_still_parsed(nodes):
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")
    _recorded(extractor, '```json\n{"edges": []}\n```')
    assert extractor.extract(nodes, "Section 12", "test doc") == []


def test_unparseable_response_raises_a_named_error_rather_than_returning_junk(nodes):
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")
    _recorded(extractor, "I'm afraid I can't help with that.")
    with pytest.raises(ExtractorError):
        extractor.extract(nodes, "Section 12", "test doc")


def test_empty_window_costs_nothing(nodes):
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")

    def explode(messages, labels):  # pragma: no cover - must never run
        raise AssertionError("no call should be made for an empty window")

    extractor._complete = explode  # type: ignore[method-assign]
    assert extractor.extract([], "Section 12", "test doc") == ()


def test_model_id_and_prompt_hash_identify_who_and_what_separately():
    extractor = LiteLLMEdgeExtractor(model="groq/llama-3.3-70b-versatile")
    other = LiteLLMEdgeExtractor(model="gemini/gemini-3.6-flash")
    assert extractor.model_id != other.model_id
    # Same instructions, different responder: the prompt hash must not move,
    # or the version key stops identifying the prompt program.
    assert extractor.prompt_hash == other.prompt_hash == prompt_hash()


def test_wire_schema_closes_both_the_type_enum_and_the_label_enum(nodes):
    schema = response_json_schema(list(ref_labels(nodes)))
    props = schema["$defs"]["ExtractedEdge"]["properties"]
    assert props["type"]["enum"] == ["exception_of", "supersedes", "defines", "none"]
    assert props["src_ref"]["enum"] == list(ref_labels(nodes))


# ---------------------------------------------------------------------------
# The structured-output latch: what is allowed to trip it
# ---------------------------------------------------------------------------


class _Rate(Exception):
    status_code = 429


class _Refused(Exception):
    """What a provider without json_schema support actually returns: a 400."""

    status_code = 400


def _fake_litellm(monkeypatch, responses):
    """Install a stand-in `litellm` module whose `completion` pops `responses`.

    Drives the REAL `_complete`, which is the only way to exercise the latch —
    the other tests in this file replace `_complete` wholesale.
    """
    import sys
    import types

    seen: list[dict] = []
    module = types.ModuleType("litellm")

    def completion(**kwargs):
        seen.append(kwargs)
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=outcome))]
        )

    module.completion = completion  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", module)
    return seen


def test_a_rate_limit_does_not_permanently_downgrade_the_run_to_json_mode(nodes, monkeypatch):
    """Measured on the first real corpus run.

    One 429 flipped `_structured_output` off for the whole pass; a later
    section then came back using `rel_type` instead of `type` and was lost to
    a validation error. A 429 is evidence about the provider's load, never
    about its schema support, so it must propagate as a failed section (which
    `dge.l3.run` records) and leave the latch alone.
    """
    seen = _fake_litellm(monkeypatch, [_Rate("429"), '{"edges": []}'])
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")

    with pytest.raises(_Rate):
        extractor.extract(nodes, "Section 12", "test doc")
    assert len(seen) == 1, "a 429 must not be retried into a second billed call"
    assert extractor._structured_output is True

    assert extractor.extract(nodes, "Section 12", "test doc") == []
    assert seen[-1]["response_format"]["type"] == "json_schema"


def test_a_provider_that_rejects_the_schema_still_falls_back_to_json_mode(nodes, monkeypatch):
    seen = _fake_litellm(monkeypatch, [_Refused("no json_schema"), '{"edges": []}'])
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")

    assert extractor.extract(nodes, "Section 12", "test doc") == []
    assert extractor._structured_output is False
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[1]["response_format"] == {"type": "json_object"}


def test_pacing_spaces_calls_out_by_the_configured_interval(nodes, monkeypatch):
    """A free-tier TPM cap is hit by burst; `min_interval_s` is the knob that
    keeps a corpus run under it (decisions.md 2026-08-21, and NEXT_STEPS'
    `DGE_PROBE_PACE_S`). The first call never waits; each one after it sleeps
    off the remainder of the interval.
    """
    import dge.adapters.extract_llm as mod

    clock = [1000.0]
    slept: list[float] = []
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))

    _fake_litellm(monkeypatch, ['{"edges": []}'] * 3)
    extractor = LiteLLMEdgeExtractor(model="fake/recorded", min_interval_s=12.0)

    extractor.extract(nodes, "Section 12", "test doc")
    assert slept == []  # nothing scheduled yet

    clock[0] += 4.0  # 4s of real work elapsed
    extractor.extract(nodes, "Section 12", "test doc")
    assert slept == [pytest.approx(8.0)]  # 12 - 4 still owed

    clock[0] += 20.0  # slow section overran the interval
    extractor.extract(nodes, "Section 12", "test doc")
    assert slept == [pytest.approx(8.0)]  # no negative sleep added


def test_pacing_is_off_by_default(nodes, monkeypatch):
    import dge.adapters.extract_llm as mod

    monkeypatch.setattr(mod.time, "sleep",
                        lambda s: pytest.fail(f"unpaced extractor slept {s}s"))
    _fake_litellm(monkeypatch, ['{"edges": []}'] * 2)
    extractor = LiteLLMEdgeExtractor(model="fake/recorded")
    extractor.extract(nodes, "Section 12", "test doc")
    extractor.extract(nodes, "Section 12", "test doc")


def test_wire_schema_marks_every_property_required(nodes):
    """Strict structured output rejects a schema whose `required` is partial.

    `confidence` has a default, so Pydantic leaves it out of `required` — valid
    JSON Schema, and a 400 from every strict implementation:

        `required` is required to be supplied and to be an array including
        every key in properties

    Groq returned exactly that for `openai/gpt-oss-120b`, and because the
    adapter reads a 400 as "this provider cannot do json_schema" it latched
    into plain JSON mode for the whole run — costing every remaining call the
    closed label enum. The wire schema tightens; `ExtractedEdge` keeps the
    default, so a response omitting `confidence` still parses.
    """
    schema = response_json_schema(list(ref_labels(nodes)))
    edge = schema["$defs"]["ExtractedEdge"]
    assert set(edge["required"]) == set(edge["properties"])
    assert "confidence" in edge["required"]
    assert set(schema["required"]) == set(schema["properties"])

    # Lenient on what we accept, strict on what we ask for.
    parsed = ExtractionResponse.model_validate_json(
        '{"edges": [{"src_ref": "N1", "dst_ref": "N2", "type": "defines",'
        ' "evidence_span": "some verbatim text here"}]}'
    )
    assert parsed.edges[0].confidence == 0.5
