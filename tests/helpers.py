import re
from collections.abc import Mapping
from typing import Any

import httpx2
from pydantic_core import from_json
from typing_extensions import override

from typesafe_sdk import (
    Asset,
    AsyncTypeSafeClient,
    Content,
    JSONValue,
    ModelMetadata,
    Questions,
    RetryPolicy,
    SystemOneResponse,
    TypeSafeClient,
)


def multipart_parts(request: httpx2.Request) -> dict[str, tuple[bytes, bytes]]:
    """Split a `multipart/form-data` request body into its parts' headers and contents, by part name."""
    boundary = request.headers["content-type"].partition("boundary=")[2].encode()
    chunks = request.content.split(b"--" + boundary)
    assert chunks[0] == b""
    assert chunks[-1] == b"--\r\n"
    parts = {}
    for chunk in chunks[1:-1]:
        head, _, content = chunk.removeprefix(b"\r\n").removesuffix(b"\r\n").partition(b"\r\n\r\n")
        match = re.search(rb'name="([^"]+)"', head)
        assert match is not None
        parts[match[1].decode()] = (head, content)
    return parts


def request_body(request: httpx2.Request) -> Any:
    """Decode a request's JSON body; for a multipart upload, each asset's `"part"` is replaced by its `"data"` bytes and `"content_type"`."""
    if not request.headers["content-type"].startswith("multipart/form-data"):
        return from_json(request.content)
    parts = multipart_parts(request)
    body = from_json(parts["request"][1])
    for entry in body["assets"].values():
        assert "format" not in entry
        head, entry["data"] = parts[entry.pop("part")]
        match = re.search(rb"Content-Type: ([^\r\n]+)", head)
        assert match is not None
        entry["content_type"] = match[1].decode()
    return body


class TrackingTransport(httpx2.MockTransport):
    close_calls = 0
    aclose_calls = 0

    @override
    def close(self) -> None:
        self.close_calls += 1

    @override
    async def aclose(self) -> None:
        self.aclose_calls += 1


async def models(
    client: TypeSafeClient | AsyncTypeSafeClient,
    *,
    retry: RetryPolicy | None = None,
    timeout: float | httpx2.Timeout | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> tuple[ModelMetadata, ...]:
    if isinstance(client, AsyncTypeSafeClient):
        return (await client.models.list(retry=retry, timeout=timeout, extra_headers=extra_headers)).models
    else:
        return client.models.list(retry=retry, timeout=timeout, extra_headers=extra_headers).models


async def system_one(
    client: TypeSafeClient | AsyncTypeSafeClient,
    *,
    state: Content,
    questions: Questions,
    model: str | None = None,
    assets: Mapping[str, Asset] | None = None,
    extra_body: Mapping[str, JSONValue | None] | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | httpx2.Timeout | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> SystemOneResponse:
    if isinstance(client, AsyncTypeSafeClient):
        return await client.system_one(
            state,
            questions,
            model=model,
            assets=assets,
            extra_body=extra_body,
            retry=retry,
            timeout=timeout,
            extra_headers=extra_headers,
        )
    else:
        return client.system_one(
            state,
            questions,
            model=model,
            assets=assets,
            extra_body=extra_body,
            retry=retry,
            timeout=timeout,
            extra_headers=extra_headers,
        )
