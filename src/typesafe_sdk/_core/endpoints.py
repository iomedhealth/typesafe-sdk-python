"""Endpoint-specific request builders, layered over the generic `transport.prepare`."""

from collections.abc import Mapping, Sequence
from typing import Any

import httpx2

from typesafe_sdk._core.batch_types import SystemOneBatchItem
from typesafe_sdk._core.config import Config
from typesafe_sdk._core.constants import MODELS_PATH, SYSTEM_ONE_BATCH_MAX_REQUESTS, SYSTEM_ONE_BATCH_PATH, SYSTEM_ONE_PATH
from typesafe_sdk._core.errors import TypeSafeError
from typesafe_sdk._core.json_types import JSONContent, JSONValue
from typesafe_sdk._core.question_types import Question
from typesafe_sdk._core.questions import normalize_questions
from typesafe_sdk._core.response_types import ListModelsResponse
from typesafe_sdk._core.transport import Request, ResponseT, prepare


def prepare_system_one(
    config: Config,
    state: JSONContent,
    questions: Mapping[str, Question],
    model: str | None,
    extra_body: Mapping[str, JSONValue | None] | None,
    timeout: float | httpx2.Timeout | None,
    headers: Mapping[str, str] | None,
    response_type: type[ResponseT],
) -> Request[ResponseT]:
    if state is None:
        raise TypeSafeError("State is required.")
    body: dict[str, Any] = {
        "state": state,
        "model": config.default_model if model is None else model,
        "questions": normalize_questions(questions),
    }
    if extra_body is not None:
        body.update(extra_body)
    return prepare(config, "POST", SYSTEM_ONE_PATH, body, timeout, headers, response_type)


def prepare_system_one_batch(
    config: Config,
    requests: Sequence[SystemOneBatchItem],
    model: str | None,
    extra_body: Mapping[str, JSONValue | None] | None,
    timeout: float | httpx2.Timeout | None,
    headers: Mapping[str, str] | None,
    response_type: type[ResponseT],
) -> Request[ResponseT]:
    if not requests:
        raise TypeSafeError("At least one request is required.")
    if len(requests) > SYSTEM_ONE_BATCH_MAX_REQUESTS:
        raise TypeSafeError(f"At most {SYSTEM_ONE_BATCH_MAX_REQUESTS} requests are allowed per batch; got {len(requests)}.")
    batch_model = config.default_model if model is None else model
    items: list[dict[str, Any]] = []
    for index, item in enumerate(requests):
        if not isinstance(item, Mapping):
            raise TypeSafeError(f"Request {index} must be a dictionary with state and questions.")
        state = item.get("state")
        if state is None:
            raise TypeSafeError(f"State is required (request {index}).")
        item_model = item.get("model")
        try:
            questions = normalize_questions(item.get("questions") or {})
        except TypeSafeError as error:
            raise TypeSafeError(f"{error} (request {index})") from None
        items.append({"state": state, "model": batch_model if item_model is None else item_model, "questions": questions})
    body: dict[str, Any] = {"requests": items}
    if extra_body is not None:
        body.update(extra_body)
    return prepare(config, "POST", SYSTEM_ONE_BATCH_PATH, body, timeout, headers, response_type)


def prepare_models(config: Config, timeout: float | httpx2.Timeout | None, headers: Mapping[str, str] | None) -> Request[ListModelsResponse]:
    return prepare(config, "GET", MODELS_PATH, None, timeout, headers, ListModelsResponse)
