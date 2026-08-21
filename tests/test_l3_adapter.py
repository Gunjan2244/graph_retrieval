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
from dge.l3.schema import response_json_schema
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
