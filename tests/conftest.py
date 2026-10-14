import os
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Literal

import httpx2
import pytest

from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy, TypeSafeClient
from typesafe_sdk.constants import API_KEY_ENV, BASE_URL_ENV, DEFAULT_BASE_URL, DEFAULT_MODEL_ENV, LOG_LEVEL_ENV

if sys.version_info >= (3, 14):
    from compression.zstd import decompress
else:
    from backports.zstd import decompress

DECODERS: dict[str, Callable[[bytes], bytes]] = {"zstd": decompress}

Client = TypeSafeClient | AsyncTypeSafeClient

# Delete once the production API accepts compressed request bodies.
skip_compressed_on_prod = pytest.mark.skipif(
    os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).strip().rstrip("/") == DEFAULT_BASE_URL,
    reason="The production API does not accept compressed request bodies yet",
)


class ClientFactory:
    def __init__(self, async_mode: bool) -> None:
        self.async_mode = async_mode
        self.instances: list[Client] = []

    def __call__(
        self,
        handler: Callable[[httpx2.Request], httpx2.Response],
        *,
        retries: bool = False,
        api_key: str | None = "test-key",
        model: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | httpx2.Timeout | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx2.BaseTransport | httpx2.AsyncBaseTransport | None = None,
        http_client: httpx2.Client | httpx2.AsyncClient | None = None,
        base_url: str | None = None,
        compression: Literal["zstd", False] | None = None,
    ) -> Client:
        if transport is None and http_client is None:
            transport = httpx2.MockTransport(handler)
        if retry is None and not retries:
            retry = RetryPolicy(max_retries=0)
        client: Client
        if self.async_mode:
            assert transport is None or isinstance(transport, httpx2.AsyncBaseTransport)
            assert http_client is None or isinstance(http_client, httpx2.AsyncClient)
            client = AsyncTypeSafeClient(
                api_key=api_key,
                model=model,
                retry=retry,
                timeout=timeout,
                headers=headers,
                transport=transport,
                http_client=http_client,
                base_url=base_url,
                compression=compression,
            )
        else:
            assert transport is None or isinstance(transport, httpx2.BaseTransport)
            assert http_client is None or isinstance(http_client, httpx2.Client)
            client = TypeSafeClient(
                api_key=api_key,
                model=model,
                retry=retry,
                timeout=timeout,
                headers=headers,
                transport=transport,
                http_client=http_client,
                base_url=base_url,
                compression=compression,
            )
        self.instances.append(client)
        return client


@pytest.fixture(params=[False, True], ids=["sync", "async"])
async def clients(request: pytest.FixtureRequest) -> AsyncIterator[ClientFactory]:
    factory = ClientFactory(request.param)
    yield factory
    for client in factory.instances:
        if isinstance(client, AsyncTypeSafeClient):
            await client.aclose()
        else:
            client.close()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration") is None:
        for name in (API_KEY_ENV, BASE_URL_ENV, DEFAULT_MODEL_ENV, LOG_LEVEL_ENV):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def decode_mock_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hand `MockTransport` handlers requests with decompressed bodies; `Content-Encoding` is kept to check what was sent."""
    init = httpx2.MockTransport.__init__

    def patched(self: httpx2.MockTransport, handler: Callable[[httpx2.Request], Any]) -> None:
        def decoding(request: httpx2.Request) -> Any:
            encoding = request.headers.get("content-encoding")
            if encoding is None:
                return handler(request)
            headers = httpx2.Headers(request.headers)
            del headers["content-length"]
            content = DECODERS[encoding](request.content)
            return handler(httpx2.Request(request.method, request.url, headers=headers, content=content, extensions=request.extensions))

        init(self, decoding)

    monkeypatch.setattr(httpx2.MockTransport, "__init__", patched)


@pytest.fixture
def live_api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        pytest.skip(f"{API_KEY_ENV} is not set")
    return key


@pytest.fixture(params=[pytest.param(None, marks=skip_compressed_on_prod, id="compressed"), pytest.param(False, id="uncompressed")])
def live_compression(request: pytest.FixtureRequest) -> Literal[False] | None:
    return request.param


@pytest.fixture(params=["sync", "async"])
async def live_client(request: pytest.FixtureRequest, live_api_key: str, live_compression: Literal[False] | None) -> AsyncIterator[Client]:
    if request.param == "async":
        async with AsyncTypeSafeClient(api_key=live_api_key, timeout=120, compression=live_compression) as client:
            yield client
    else:
        with TypeSafeClient(api_key=live_api_key, timeout=120, compression=live_compression) as client:
            yield client
