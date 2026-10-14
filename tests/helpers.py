from collections.abc import Mapping, Sequence

import httpx2
from typing_extensions import override

from typesafe_sdk import (
    AsyncTypeSafeClient,
    JSONContent,
    JSONValue,
    ModelMetadata,
    Questions,
    RetryPolicy,
    SystemOneBatchItem,
    SystemOneBatchResponse,
    SystemOneResponse,
    TypeSafeClient,
)


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
    state: JSONContent,
    questions: Questions,
    model: str | None = None,
    extra_body: Mapping[str, JSONValue | None] | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | httpx2.Timeout | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> SystemOneResponse:
    if isinstance(client, AsyncTypeSafeClient):
        return await client.system_one(
            state, questions, model=model, extra_body=extra_body, retry=retry, timeout=timeout, extra_headers=extra_headers
        )
    else:
        return client.system_one(
            state, questions, model=model, extra_body=extra_body, retry=retry, timeout=timeout, extra_headers=extra_headers
        )


async def system_one_batch(
    client: TypeSafeClient | AsyncTypeSafeClient,
    *,
    requests: Sequence[SystemOneBatchItem],
    model: str | None = None,
    extra_body: Mapping[str, JSONValue | None] | None = None,
    retry: RetryPolicy | None = None,
    timeout: float | httpx2.Timeout | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> SystemOneBatchResponse:
    if isinstance(client, AsyncTypeSafeClient):
        return await client.system_one_batch(
            requests, model=model, extra_body=extra_body, retry=retry, timeout=timeout, extra_headers=extra_headers
        )
    else:
        return client.system_one_batch(
            requests, model=model, extra_body=extra_body, retry=retry, timeout=timeout, extra_headers=extra_headers
        )
