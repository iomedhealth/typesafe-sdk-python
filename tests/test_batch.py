"""The batch System One endpoint, an extension of self-hosted System One servers."""

import logging
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel
from pydantic_core import from_json
from typing_extensions import assert_type

from tests.conftest import ClientFactory
from tests.helpers import system_one_batch
from tests.test_clients import RESULT
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    SystemOneBatchItem,
    SystemOneBatchResponse,
    SystemOneResponse,
    TypeSafeAPIError,
    TypeSafeAPIResponseValidationError,
    TypeSafeAuthenticationError,
    TypeSafeBadRequestError,
    TypeSafeError,
    TypeSafeInternalServerError,
    TypeSafeRateLimitError,
    TypeSafeUnprocessableEntityError,
    Usage,
)

BATCH_URL = "https://api.typesafe.ai/v1/systemone/batch"
SECOND = {
    "model": "jev-latest",
    "usage": {"input_tokens": 7, "output_tokens": 0},
    "answers": {"spam": {"type": "noul", "noul": 0.02}},
}
BATCH_RESULT: dict[str, Any] = {
    "model": "jev-latest",
    "results": [RESULT, SECOND],
    "usage": {"latency_ms": 12.5, "input_tokens": 19, "output_tokens": 0},
}
ITEMS: list[SystemOneBatchItem] = [
    {
        "state": {"document": "Hello 🌍"},
        "questions": {
            "spam": Noul(instructions="Spam?"),
            "tone": Choice(instructions="Tone?", criteria={"friendly": None, "hostile": None}),
            "quality": Score(instructions="Quality?", criteria=["bad", "ok", "great"]),
        },
    },
    {"state": "Second document", "questions": {"spam": {"type": "noul", "instructions": "Spam?"}}, "model": "jev-fast"},
]
ONE_ITEM: list[SystemOneBatchItem] = [{"state": "x", "questions": {"spam": Noul(instructions="Spam?")}}]


def batch_handler(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=BATCH_RESULT, headers={"x-typesafe-request-id": "req-batch"})


@pytest.mark.parametrize("model", [None, "jev-batch"])
async def test_round_trip(clients: ClientFactory, model: str | None) -> None:
    expected: dict[str, Any] = {
        "requests": [
            {
                "state": {"document": "Hello 🌍"},
                "model": "jev-latest" if model is None else model,
                "questions": {
                    "spam": {"type": "noul", "instructions": "Spam?"},
                    "tone": {"type": "choice", "instructions": "Tone?", "criteria": {"friendly": None, "hostile": None}},
                    "quality": {"type": "score", "instructions": "Quality?", "criteria": ["bad", "ok", "great"]},
                },
            },
            {"state": "Second document", "model": "jev-fast", "questions": {"spam": {"type": "noul", "instructions": "Spam?"}}},
        ]
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST"
        assert str(request.url) == BATCH_URL
        assert from_json(request.content) == expected
        return batch_handler(request)

    result = await system_one_batch(clients(handler), requests=ITEMS, model=model)
    assert_type(result, SystemOneBatchResponse)
    assert result.model == "jev-latest"
    assert result.usage == Usage(input_tokens=19, output_tokens=0)
    assert result.request_id == "req-batch"
    assert len(result.results) == 2
    first, second = result.results
    assert isinstance(first, SystemOneResponse)
    assert first.nouls == {"spam": NoulAnswer(noul=0.98)}
    assert first.choices == {"tone": ChoiceAnswer(choice="friendly", confidence=0.9, probabilities={"friendly": 0.9, "hostile": 0.1})}
    assert first.scores == {
        "quality": ScoreAnswer(score=1.7, confidence=0.8, legend={0: "bad", 1: "ok", 2: "great"}, probabilities={0: 0.1, 1: 0.1, 2: 0.8})
    }
    assert first.usage == Usage(input_tokens=12, output_tokens=3)
    assert second.nouls["spam"].noul == 0.02
    assert second.choices == {}
    assert result.raw_http_response.json() == BATCH_RESULT


async def test_client_default_model(clients: ClientFactory) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert [item["model"] for item in from_json(request.content)["requests"]] == ["client-model"]
        return batch_handler(request)

    await system_one_batch(clients(handler, model="client-model"), requests=ONE_ITEM)


async def test_extra_body_shallow_override(clients: ClientFactory) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert from_json(request.content) == {"requests": "override", "beam_width": 4, "nullable": None}
        return batch_handler(request)

    await system_one_batch(clients(handler), requests=ONE_ITEM, extra_body={"requests": "override", "beam_width": 4, "nullable": None})


@pytest.mark.parametrize(
    "requests,match",
    [
        ([], "At least one request"),
        ([*ONE_ITEM] * 51, "At most 50 requests are allowed per batch; got 51"),
        ([*ONE_ITEM, {"state": None, "questions": {"spam": Noul(instructions="Spam?")}}], r"State is required \(request 1\)"),
        ([{"state": "x", "questions": {}}], r"At least one question is required. \(request 0\)"),
        (
            [*ONE_ITEM, {"state": "x", "questions": {"rating": {"type": "score", "instructions": "?", "criteria": []}}}],
            '"rating" has no criteria',
        ),
        ([{"state": "x", "questions": {"spam": Noul.model_validate({})}}], '"spam" has neither instructions nor criteria'),
        (["not a mapping"], "Request 0 must be a dictionary"),
    ],
)
async def test_validation_before_network(clients: ClientFactory, requests: Any, match: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid request reached the network")

    with pytest.raises(TypeSafeError, match=match):
        await system_one_batch(clients(handler), requests=requests)


@pytest.mark.parametrize(
    "status,error",
    [
        (400, TypeSafeBadRequestError),
        (401, TypeSafeAuthenticationError),
        (422, TypeSafeUnprocessableEntityError),
        (429, TypeSafeRateLimitError),
        (500, TypeSafeInternalServerError),
        (503, TypeSafeInternalServerError),
        (409, TypeSafeAPIError),
    ],
)
async def test_error_mapping(clients: ClientFactory, status: int, error: type[TypeSafeAPIError]) -> None:
    body = {"detail": {"message": "Server explanation"}}
    with pytest.raises(error) as caught:
        await system_one_batch(
            clients(lambda request: httpx2.Response(status, json=body, headers={"x-typesafe-request-id": "req_123"})), requests=ONE_ITEM
        )
    assert type(caught.value) is error
    assert caught.value.status == status
    assert caught.value.body == body
    assert str(caught.value) == f"POST {BATCH_URL}: {status} Server explanation (request_id=req_123)"


@pytest.mark.parametrize(
    "body,field_path",
    [
        ([], ""),
        ({"results": [RESULT], "usage": {}}, "model"),
        ({"model": "m", "usage": {}}, "results"),
        ({"model": "m", "results": "nope", "usage": {}}, "results"),
        ({"model": "m", "results": [RESULT, "nope"], "usage": {}}, "results[1]"),
        ({"model": "m", "results": [RESULT, {"usage": {}, "answers": {}}], "usage": {}}, "results[1].model"),
        ({"model": "m", "results": [{"model": "m", "usage": {}, "answers": {"n": "bad"}}], "usage": {}}, "results[0].answers.n.type"),
        (
            {"model": "m", "results": [RESULT, {"model": "m", "usage": {}, "answers": {"n": {"type": "noul"}}}], "usage": {}},
            "results[1].answers.n.noul",
        ),
        (
            {
                "model": "m",
                "results": [RESULT, {"model": "m", "usage": {}, "answers": {"n": {"type": "noul", "noul": "0.5"}}}],
                "usage": {},
            },
            "results[1].answers.n.noul",
        ),
        (
            {
                "model": "m",
                "results": [{"model": "m", "usage": {}, "answers": {"c": {"type": "choice", "choice": "a", "probabilities": {}}}}],
                "usage": {},
            },
            "results[0].answers.c.confidence",
        ),
    ],
)
async def test_malformed_response_raises_validation_error(clients: ClientFactory, body: Any, field_path: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=body, headers={"x-typesafe-request-id": "req-123"})

    with pytest.raises(TypeSafeAPIResponseValidationError) as caught:
        await system_one_batch(clients(handler), requests=ONE_ITEM)
    assert caught.value.field_path == field_path
    assert caught.value.status == 200
    assert caught.value.request_id == "req-123"
    assert caught.value.body == body
    assert str(caught.value) == f"POST {BATCH_URL}: 200 Invalid response data at {field_path!r}. (request_id=req-123)"


async def test_unknown_answer_types_are_dropped_per_result(clients: ClientFactory, caplog: pytest.LogCaptureFixture) -> None:
    future = {"model": "m", "usage": {}, "answers": {"spam": {"type": "noul", "noul": 0.1}, "future": {"type": "future", "value": 1}}}
    body = {**BATCH_RESULT, "results": [RESULT, future]}
    with caplog.at_level(logging.WARNING, logger="typesafe_sdk"):
        result = await system_one_batch(clients(lambda request: httpx2.Response(200, json=body)), requests=ONE_ITEM)
    assert set(result.results[1].answers) == {"spam"}
    assert "Ignoring answer 'future' with unrecognized type 'future'" in caplog.text
    assert result.raw_http_response.json() == body


async def test_empty_results_and_missing_usage_fields(clients: ClientFactory) -> None:
    body: dict[str, Any] = {"model": "m", "results": [], "usage": {}}
    result = await system_one_batch(clients(lambda request: httpx2.Response(200, json=body)), requests=ONE_ITEM)
    assert result.results == []
    assert result.usage == Usage()


class KnownAnswers(BaseModel):
    spam: NoulAnswer


class KnownResponse(BaseModel):
    model: str
    answers: KnownAnswers


class KnownBatchResponse(BaseModel):
    model: str
    results: list[KnownResponse]


class TypedSystemOneResponse(SystemOneResponse):
    spam: NoulAnswer
    missing: NoulAnswer | None = None


class TypedBatchResponse(SystemOneBatchResponse):
    results: list[TypedSystemOneResponse]  # pyrefly: ignore[bad-override]


async def test_standalone_pydantic_response_model(clients: ClientFactory) -> None:
    client = clients(batch_handler)
    if isinstance(client, AsyncTypeSafeClient):
        result = await client.system_one_batch(ONE_ITEM, response_model=KnownBatchResponse)
    else:
        result = client.system_one_batch(ONE_ITEM, response_model=KnownBatchResponse)
    assert_type(result, KnownBatchResponse)
    assert type(result) is KnownBatchResponse
    assert [item.answers.spam.noul for item in result.results] == [0.98, 0.02]


@pytest.mark.parametrize("response_model", [None, SystemOneBatchResponse])
async def test_explicit_default_response_model(clients: ClientFactory, response_model: type[SystemOneBatchResponse] | None) -> None:
    client = clients(batch_handler)
    if isinstance(client, AsyncTypeSafeClient):
        result = await client.system_one_batch(ONE_ITEM, response_model=response_model)
    else:
        result = client.system_one_batch(ONE_ITEM, response_model=response_model)
    assert isinstance(result, SystemOneBatchResponse)
    assert result.results[0].nouls["spam"].noul == 0.98
    assert result.request_id == "req-batch"


async def test_batch_response_subclass_lifts_answer_fields(clients: ClientFactory) -> None:
    client = clients(batch_handler)
    if isinstance(client, AsyncTypeSafeClient):
        result = await client.system_one_batch(ONE_ITEM, response_model=TypedBatchResponse)
    else:
        result = client.system_one_batch(ONE_ITEM, response_model=TypedBatchResponse)
    assert type(result) is TypedBatchResponse
    assert [type(item) for item in result.results] == [TypedSystemOneResponse, TypedSystemOneResponse]
    assert [item.spam.noul for item in result.results] == [0.98, 0.02]
    assert result.results[0].missing is None
    assert result.results[0].nouls["spam"].noul == 0.98


async def test_response_serialization_excludes_http_metadata(clients: ClientFactory) -> None:
    result = await system_one_batch(clients(batch_handler), requests=ONE_ITEM)
    assert result.results[0].choices
    assert set(result.model_dump()) == set(BATCH_RESULT)
    encoded = result.model_dump_json()
    restored = SystemOneBatchResponse.model_validate_json(encoded)
    assert restored == result
