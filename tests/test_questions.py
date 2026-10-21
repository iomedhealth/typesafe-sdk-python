import copy
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast

import httpx2
import pytest
from pydantic import BaseModel, ValidationError
from pydantic_core import from_json, to_json

from tests.conftest import ClientFactory
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Box,
    Choice,
    ChoiceModel,
    Noul,
    NoulCriteria,
    NoulModel,
    Question,
    Questions,
    Score,
    ScoreModel,
    TypeSafeError,
)
from typesafe_sdk._core.questions import normalize_questions
from typesafe_sdk._schemas import models as wire


def test_normalization_preserves_objects() -> None:
    questions = {
        "noul": Noul(instructions="Spam?"),
        "choice": Choice(instructions="Tone?", criteria={"calm": None}),
        "score": Score(instructions="Quality?", criteria=["bad", "good"]),
        "box": Box(instructions="The dog"),
    }
    result = normalize_questions(questions)
    for name, question in questions.items():
        assert result[name] is question
    assert from_json(to_json(result)) == {
        "noul": {"type": "noul", "instructions": "Spam?"},
        "choice": {"type": "choice", "instructions": "Tone?", "criteria": {"calm": None}},
        "score": {"type": "score", "instructions": "Quality?", "criteria": ["bad", "good"]},
        "box": {"type": "box", "instructions": "The dog"},
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "noul", "instructions": "Spam?", "weight": 3, "criteria": {"future": "kept"}},
        {"type": "future", "nested": {"k": None}},
        {"type": "score", "criteria": ["good"], "weight": 3},
        {"type": "box", "instructions": {"find": "the dog"}},
    ],
)
def test_normalization_preserves_raw_questions(raw: dict[str, Any]) -> None:
    before = copy.deepcopy(raw)
    questions = cast(Questions, {"raw": raw, "typed": Noul(instructions="Spam?")})
    result = normalize_questions(questions)
    assert isinstance(result["typed"], wire.NoulQuestion)
    assert from_json(to_json(result)) == {"raw": raw, "typed": {"type": "noul", "instructions": "Spam?"}}
    assert raw == before
    assert result["raw"] is raw


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"instructions": "Missing type"},
        {"type": "choice"},
        {"type": "score"},
        {"type": ""},
        {"type": None},
        {"type": 1},
        {"type": ["future"]},
        "noul",
        None,
    ],
)
def test_raw_questions_require_structural_keys(invalid: object) -> None:
    with pytest.raises(TypeSafeError, match='Question "invalid"'):
        normalize_questions(cast(Questions, {"invalid": invalid}))


@pytest.mark.parametrize(
    "question,expected",
    [
        (Noul(criteria={"true": "spam"}), {"type": "noul", "criteria": {"true": "spam"}}),
        (Choice(criteria={"a": None}), {"type": "choice", "criteria": {"a": None}}),
        (Score(criteria=["good"]), {"type": "score", "criteria": ["good"]}),
        (Box(), {"type": "box"}),
        (Box(instructions=["The", None]), {"type": "box", "instructions": ["The", None]}),
        (Noul(instructions="", criteria={}), {"type": "noul", "instructions": "", "criteria": {}}),
        (Noul(instructions=[], criteria={"true": None}), {"type": "noul", "instructions": [], "criteria": {"true": None}}),
    ],
)
def test_direct_encoding_omits_only_default_fields(question: Noul | Choice | Score | Box, expected: dict[str, Any]) -> None:
    assert from_json(to_json(question)) == expected
    assert question.model_dump() == expected


def test_discriminators_are_automatic() -> None:
    noul = Noul(instructions="Spam?")
    choice = Choice(instructions="Tone?", criteria={"calm": None})
    score = Score(instructions="Quality?", criteria=["good"])
    for question, wire_type, tag in (
        (noul, wire.NoulQuestion, "noul"),
        (choice, wire.ChoiceQuestion, "choice"),
        (score, wire.ScoreQuestion, "score"),
    ):
        assert isinstance(question, BaseModel)
        assert isinstance(question, wire_type)
        assert question.type == tag
        assert question.model_dump()["type"] == tag
        assert from_json(to_json(question))["type"] == tag
        # Construction is keyword-only: a positional argument is rejected.
        with pytest.raises(TypeError):
            cast(Any, type(question))("Spam?")
        question.instructions = "Updated?"
        assert question.model_dump()["instructions"] == "Updated?"


def test_invalid_typed_question_is_rejected_on_construction() -> None:
    # Unlike raw dictionaries, typed questions validate eagerly rather than deferring to the API.
    with pytest.raises(ValidationError):
        Choice(criteria=cast(Any, ["invalid", "shape"]))


@pytest.mark.parametrize(
    "question_type,kwargs",
    [
        (Noul, {}),
        (Choice, {"criteria": {"a": None}}),
        (Score, {"criteria": ["good"]}),
        (Box, {}),
    ],
)
def test_typed_questions_reject_unknown_fields(question_type: Any, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        question_type(**kwargs, unexpected=True)


@pytest.mark.parametrize("raw", [False, True])
@pytest.mark.parametrize(
    "criteria",
    [
        None,
        {},
        {"true": "Yes"},
        {"false": "No"},
        {"true": "Yes", "false": "No"},
        {"true": {"summary": "Unsolicited", "examples": ["Buy now"]}},
    ],
)
def test_optional_noul_criteria(raw: bool, criteria: NoulCriteria | None) -> None:
    expected: NoulModel = {"type": "noul", "instructions": "Spam?"}
    if criteria is not None:
        expected["criteria"] = criteria
    question: Question
    if raw:
        question = expected.copy()
    else:
        question = Noul(instructions="Spam?", criteria=criteria)
    assert from_json(to_json(normalize_questions({"q": question}))) == {"q": expected}


def test_typed_noul_criteria_reject_unknown_fields() -> None:
    criteria = cast(NoulCriteria, {"true": "yes", "metadata": {"source": None}})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Noul(instructions="Spam?", criteria=criteria)


def test_empty_score_criteria_is_rejected() -> None:
    with pytest.raises(ValidationError, match="too_short"):
        Score(instructions="Quality?", criteria=[])
    with pytest.raises(TypeSafeError, match='"rating" has no criteria'):
        normalize_questions({"rating": {"type": "score", "instructions": "Quality?", "criteria": []}})


@pytest.mark.parametrize("raw", [False, True])
def test_empty_choice_criteria_is_rejected(raw: bool) -> None:
    model: ChoiceModel = {"type": "choice", "instructions": "Tone?", "criteria": {}}
    question = model if raw else Choice(instructions=model["instructions"], criteria=model["criteria"])
    with pytest.raises(TypeSafeError, match='"label" has no criteria'):
        normalize_questions({"label": question})


@pytest.mark.parametrize(
    "question",
    [
        Noul.model_validate({}),
        Noul.model_validate({"criteria": {}}),
        Noul.model_validate({"criteria": {"true": None, "false": None}}),
        {"type": "noul"},
        {"type": "noul", "instructions": None, "criteria": {}},
        {"type": "noul", "criteria": {"true": None, "false": None}},
    ],
)
def test_noul_without_instructions_or_criteria_is_rejected(question: Question) -> None:
    with pytest.raises(TypeSafeError, match='"spam" has neither instructions nor criteria'):
        normalize_questions({"spam": question})


def test_raw_noul_criteria_must_be_an_object() -> None:
    with pytest.raises(TypeSafeError, match='"spam" criteria must be an object'):
        normalize_questions(cast(Questions, {"spam": {"type": "noul", "criteria": "spam"}}))


async def test_covariant_question_mappings(clients: ClientFactory) -> None:
    nouls = {"q": Noul(instructions="Spam?")}
    choices = {"q": Choice(instructions="Tone?", criteria={"calm": None})}
    scores = {"q": Score(instructions="Quality?", criteria=["good"])}
    raw_nouls: dict[str, NoulModel] = {"q": {"type": "noul", "instructions": "Spam?"}}
    raw_choices: dict[str, ChoiceModel] = {"q": {"type": "choice", "instructions": "Tone?", "criteria": {"calm": None}}}
    raw_scores: dict[str, ScoreModel] = {"q": {"type": "score", "instructions": "Quality?", "criteria": ["good"]}}
    read_only: Mapping[str, Choice] = MappingProxyType(choices)
    mixed: Questions = {"one": nouls["q"], "two": raw_choices["q"], "three": scores["q"]}
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert from_json(request.content)["questions"]
        return httpx2.Response(200, json={"model": "jev-latest", "usage": {}, "answers": {}})

    client = clients(handler)
    if isinstance(client, AsyncTypeSafeClient):
        await client.system_one("x", nouls)
        await client.system_one("x", choices)
        await client.system_one("x", scores)
        await client.system_one("x", raw_nouls)
        await client.system_one("x", raw_choices)
        await client.system_one("x", raw_scores)
        await client.system_one("x", read_only)
        await client.system_one("x", mixed)
        await client.system_one("x", {"q": {"type": "choice", "instructions": "Tone?", "criteria": {"calm": None}}})
    else:
        client.system_one("x", nouls)
        client.system_one("x", choices)
        client.system_one("x", scores)
        client.system_one("x", raw_nouls)
        client.system_one("x", raw_choices)
        client.system_one("x", raw_scores)
        client.system_one("x", read_only)
        client.system_one("x", mixed)
        client.system_one("x", {"q": {"type": "choice", "instructions": "Tone?", "criteria": {"calm": None}}})
    assert calls == 9
    assert scores["q"].criteria == ["good"]
    assert raw_scores["q"]["criteria"] == ["good"]
    assert read_only["q"] is choices["q"]
