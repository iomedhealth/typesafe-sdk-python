"""Endpoint-specific request builders, layered over the generic `transport.prepare`."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import httpx2

from typesafe_sdk._core.assets import REFERENCE_PATTERN, Asset, References, ResolvedImage
from typesafe_sdk._core.config import Config
from typesafe_sdk._core.constants import CONTENT_TYPE_HEADER, JSON_CONTENT_TYPE, MODELS_PATH, SYSTEM_ONE_PATH
from typesafe_sdk._core.content_types import Content
from typesafe_sdk._core.errors import TypeSafeError
from typesafe_sdk._core.json import serialize
from typesafe_sdk._core.json_types import JSONValue
from typesafe_sdk._core.multipart import Part, encode_multipart
from typesafe_sdk._core.question_types import Question
from typesafe_sdk._core.questions import normalize_questions
from typesafe_sdk._core.response_types import ListModelsResponse
from typesafe_sdk._core.transport import Request, ResponseT, prepare
from typesafe_sdk._schemas import models as wire


def prepare_system_one(
    config: Config,
    state: Content,
    questions: Mapping[str, Question],
    model: str | None,
    extra_body: Mapping[str, JSONValue | None] | None,
    timeout: float | httpx2.Timeout | None,
    headers: Mapping[str, str] | None,
    response_type: type[ResponseT],
    *,
    assets: Mapping[str, Asset] | None = None,
    attached: Mapping[str, Asset] | None = None,
) -> tuple[Request[ResponseT], dict[str, Asset] | None]:
    """Prepare a System One request without its assets.

    Returns:
        The request, its body naming each asset's part, and the assets to resolve and add with `with_assets`:
        those in `assets`, then those whose reference appears in the body, in order of appearance. `None`
        when the request sends no assets, or `extra_body` replaces them, so they aren't read or encoded.
    """
    if state is None:
        raise TypeSafeError("State is required.")
    body: dict[str, Any] = {
        "state": state,
        "model": config.default_model if model is None else model,
        "questions": normalize_questions(questions),
    }
    if extra_body is not None:
        body.update(extra_body)
    # Assets serialize as their alias in `assets`, or else as their reference, collected in `references.found`.
    references = References({id(asset): name for name, asset in (assets or {}).items()})
    request = prepare(config, "POST", SYSTEM_ONE_PATH, body, timeout, headers, response_type, context=references)
    if "assets" in body:
        return request, None
    pending = dict(assets or {})
    # Placed, passed, and attached assets are known by reference: placed ones serialize as it, and text may contain it.
    known = {**(attached or {}), **{str(asset): asset for asset in pending.values()}, **references.found}
    if known and request.content is not None:
        # References are JSON-safe ASCII, so one scan of the encoded body finds them in every string.
        for match in REFERENCE_PATTERN.finditer(request.content):
            reference = match[0].decode()
            if reference in known:
                pending.setdefault(reference, known[reference])
    if not pending:
        # The API rejects an empty `assets`, so it's left out.
        return request, None
    # Each asset's part is named by its position, so the body can name it before the asset is read.
    entries = {name: wire.Asset(type="image", part=_part_name(i)).model_dump(exclude_none=True) for i, name in enumerate(pending)}
    return replace(request, content=serialize({**body, "assets": entries}, references)), pending


def with_assets(request: Request[ResponseT], assets: Mapping[str, ResolvedImage]) -> Request[ResponseT]:
    """Add the resolved assets from `prepare_system_one` to its request.

    The JSON body becomes the first part, named `request`, of a `multipart/form-data` body, followed
    by each asset's raw bytes as the part that its entry names.
    """
    # The API requires each part to be claimed by exactly one asset, so an image under several names is sent once per name.
    # Each part is labeled with its content type when recognized, so its asset entry only names it.
    parts = [Part(_part_name(i), image.content_type, image.data, _part_name(i)) for i, image in enumerate(assets.values())]
    content_type, content = encode_multipart([Part("request", JSON_CONTENT_TYPE, request.content or b""), *parts])
    headers = httpx2.Headers(request.headers)
    headers[CONTENT_TYPE_HEADER] = content_type
    return replace(request, headers=headers, content=content)


def _part_name(index: int) -> str:
    return f"asset-{index}"


def prepare_models(config: Config, timeout: float | httpx2.Timeout | None, headers: Mapping[str, str] | None) -> Request[ListModelsResponse]:
    return prepare(config, "GET", MODELS_PATH, None, timeout, headers, ListModelsResponse)
