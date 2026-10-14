"""Asynchronous API client."""

import ssl
import threading
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from functools import cached_property
from types import TracebackType
from typing import Any, Literal, overload

import httpx2
from packaging.version import Version
from typing_extensions import Self

from typesafe_sdk._core.assets import Asset, resolve_assets_async
from typesafe_sdk._core.attached import AttachedAssets
from typesafe_sdk._core.client.aio.models import AsyncModels
from typesafe_sdk._core.config import DEFAULT_LIMITS, Config
from typesafe_sdk._core.content_types import Content
from typesafe_sdk._core.endpoints import prepare_system_one, with_assets
from typesafe_sdk._core.json_types import JSONValue
from typesafe_sdk._core.question_types import Question
from typesafe_sdk._core.response_types import SystemOneResponse
from typesafe_sdk._core.retry import RetryPolicy, build_tenacity_async
from typesafe_sdk._core.transport import Request, ResponseT, send_async


def _ssl_context() -> ssl.SSLContext:
    """httpx2's default SSL context, with `wrap_bio()` serialized on affected truststore versions.

    AnyIO calls `wrap_bio()` from worker threads, and truststore<=0.10.4 reconfigures the shared
    context there without a lock, which can abort the interpreter (sethmlarson/truststore#221).
    """
    ctx = httpx2.create_ssl_context()
    import truststore  # noqa: PLC0415 - Unavailable on emscripten; httpx2 imports it lazily too.

    if not isinstance(ctx, truststore.SSLContext) or Version(truststore.__version__) > Version("0.10.4"):
        return ctx
    wrap_bio = ctx.wrap_bio
    lock = threading.Lock()

    def locked_wrap_bio(*args: Any, **kwargs: Any) -> ssl.SSLObject:
        with lock:
            return wrap_bio(*args, **kwargs)

    ctx.wrap_bio = locked_wrap_bio
    return ctx


class AsyncTypeSafeClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | httpx2.Timeout | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        http_client: httpx2.AsyncClient | None = None,
        base_url: str | None = None,
        compression: Literal["zstd", False] | None = None,
    ) -> None:
        """Create an asynchronous HTTP client for [TypeSafe AI API](https://typesafe.ai).

        Explicit options take precedence over environment variables; empty or whitespace-only
        environment values are ignored.

        !!! tip "Logging setup"
            The SDK logs to the `typesafe_sdk` logger; configure it through standard logging, or set
            `TYPESAFE_LOG_LEVEL` (`debug`, `info`, ...) for a quick default. Secret headers are
            redacted from log output; request and response bodies are not.

        Args:
            api_key: Required API key; may be set via the `TYPESAFE_API_KEY` environment variable.
                Leading and trailing whitespace is stripped. Empty keys, internal whitespace,
                control characters, and non-ASCII characters are rejected.
            model: Model name; may be set via the `TYPESAFE_DEFAULT_MODEL` environment variable.
            retry: A `RetryPolicy` controlling retry behavior; see `RetryPolicy` for the available options and their
                defaults. Pass `RetryPolicy(max_retries=0)` to disable retries.
            timeout: Timeout for HTTP operations. Inherits `http_client.timeout` when supplied, otherwise the SDK default.
            headers: Additional request headers to set.
            transport: Optional custom HTTP transport, closed when this SDK client closes.
            http_client: Optional `httpx2.AsyncClient`; mutually exclusive with `transport`.
                Closed when this SDK client closes.
            base_url: API root; may be set via the `TYPESAFE_BASE_URL` environment variable.
            compression: Request body compression: `"zstd"`, or `False` to send bodies uncompressed. Defaults to zstd
                [when available](../../usage/advanced.md#ensure-zstd-is-available).

        Raises:
            TypeSafeError: The API key is missing or invalid, the timeout is invalid, or zstd compression is unavailable.
            ValueError: Both `transport` and `http_client` are supplied.

        Examples:
            ```python
            import asyncio

            from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul


            async def main() -> None:
                async with AsyncTypeSafeClient() as client:
                    result = await client.system_one(
                        state="I was charged twice. Please help.",
                        questions={
                            "billing": Noul(instructions="Is this about billing?"),
                            "tone": Choice(
                                instructions="What is the tone?",
                                criteria={"calm": None, "angry": None},
                            ),
                        },
                    )
                    assert 0 <= result.nouls["billing"].noul <= 1
                    assert result.choices["tone"].choice in {"calm", "angry"}


            asyncio.run(main())
            ```
        """
        if transport is not None and http_client is not None:
            raise ValueError("transport and http_client are mutually exclusive.")
        if timeout is None and http_client is not None:
            timeout = http_client.timeout
        self._config = Config.resolve(api_key, base_url, model, timeout, headers, compression)
        self._retry = build_tenacity_async(retry)
        self._attached = AttachedAssets()
        self._http_client = (
            httpx2.AsyncClient(timeout=self._config.timeout, limits=DEFAULT_LIMITS, transport=transport, verify=_ssl_context())
            if http_client is None
            else http_client
        )

    @cached_property
    def models(self) -> AsyncModels:
        """An accessor for the Models API resource.

        Examples:
            ```python
            async def main() -> None:
                async with AsyncTypeSafeClient() as client:
                    models = await client.models.list()
            ```
        """
        return AsyncModels(self._config, self._http_client, self._retry)

    def attach_assets(self, assets: Asset | Sequence[Asset]) -> AbstractContextManager[None]:
        """Attach assets to calls made within a `with` block, for referencing them inside strings.

        Until the block exits, calls on this client send each attached asset whose reference, `str(asset)`, appears in the request.

        !!! warning
            The SDK only recognizes references to assets that are attached, passed in `assets`, or
            placed directly in the content. A reference to any other asset is sent as plain text,
            without the asset.

        Args:
            assets: An asset, or a sequence of them.

        Raises:
            TypeSafeError: An attached value is not an asset.

        Examples:
            ```python
            from pathlib import Path

            from typesafe_sdk import AsyncTypeSafeClient, Image, Noul, SystemOneResponse


            async def inspect(client: AsyncTypeSafeClient, path: Path) -> SystemOneResponse:
                photo = Image(path)
                with client.attach_assets(photo):
                    return await client.system_one(
                        state=f"Product: {photo}",
                        questions={"damaged": Noul(instructions="Is the product damaged?")},
                    )
            ```
        """
        return self._attached.attach(assets)

    @overload
    async def system_one(
        self,
        state: Content,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
        assets: Mapping[str, Asset] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | httpx2.Timeout | None = None,
        extra_headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, JSONValue | None] | None = None,
        response_model: None = None,
    ) -> SystemOneResponse: ...

    @overload
    async def system_one(
        self,
        state: Content,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
        assets: Mapping[str, Asset] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | httpx2.Timeout | None = None,
        extra_headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, JSONValue | None] | None = None,
        response_model: type[ResponseT],
    ) -> ResponseT: ...

    async def system_one(
        self,
        state: Content,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
        assets: Mapping[str, Asset] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | httpx2.Timeout | None = None,
        extra_headers: Mapping[str, str] | None = None,
        extra_body: Mapping[str, JSONValue | None] | None = None,
        response_model: type[ResponseT] | None = None,
    ) -> SystemOneResponse | ResponseT:
        """Answer named questions about text, structured state, or assets.

        See [System One](https://docs.typesafe.ai/go/system-one) for details.

        Args:
            state: Text, an asset, a JSON object, or an array to evaluate.
                See [state](https://docs.typesafe.ai/go/state) for details.
            questions: Nonempty mapping of names to question objects or raw dictionaries.
            model: Model override; `None` inherits the client default.
            assets: Assets keyed by the reference used for them in `state` or questions;
                see [Attaching media assets](../../usage/basic.md#attaching-media-assets).
            retry: An optional retry policy to override the client-level value for this call only.
            timeout: An optional timeout for http operations to override the client-level value for this call only, in seconds.
            extra_headers: Additional request headers to set.
            extra_body: Additional top-level request-body fields, shallow-merged over the body after
                `state`, `model`, `questions`, and `assets` are set. Merging is last-write-wins: a key that
                collides with those fields overrides it, and object values are
                replaced rather than deep-merged.
            response_model: Optional Pydantic `BaseModel` type describing the
                JSON response body, including any nested answer models.

        Returns:
            An instance of `response_model`, or `SystemOneResponse` with answers keyed by question
            name and model and token usage details when no custom model is supplied.

        Raises:
            TypeSafeError: The request is invalid before it's sent, such as empty questions or an unreadable asset.
            TypeSafeAPIError: The server returns an unsuccessful HTTP response after any retries.
            TypeSafeAPIConnectionError: The request cannot connect or times out after any retries.
            TypeSafeAPIResponseValidationError: The response body does not match the response model.

        Examples:
            Create questions with named arguments:

            ```python
            async def main() -> None:
                async with AsyncTypeSafeClient() as client:
                    result = await client.system_one(
                        state="I was charged twice. Please help.",
                        questions={
                            "billing": Noul(instructions="Is this about billing?"),
                            "tone": Choice(
                                instructions="What is the tone?",
                                criteria={"calm": None, "angry": None},
                            ),
                        },
                    )
                    assert 0 <= result.nouls["billing"].noul <= 1
                    assert result.choices["tone"].choice in {"calm", "angry"}
            ```

            Pass questions as dictionaries:

            ```python
            async def main() -> None:
                async with AsyncTypeSafeClient() as client:
                    result = await client.system_one(
                        state={"message": "I was charged twice. Please help."},
                        questions={
                            "billing": {"type": "noul", "instructions": "Is this about billing?"},
                            "tone": {
                                "type": "choice",
                                "instructions": "What is the tone?",
                                "criteria": {"calm": None, "angry": None},
                            },
                        },
                    )
                    assert 0 <= result.nouls["billing"].noul <= 1
                    assert result.choices["tone"].choice in {"calm", "angry"}
            ```
        """
        request, pending = prepare_system_one(
            self._config,
            state,
            questions,
            model,
            extra_body,
            timeout,
            extra_headers,
            SystemOneResponse if response_model is None else response_model,
            assets=assets,
            attached=self._attached.snapshot(),
        )
        if pending is not None:
            request = with_assets(request, await resolve_assets_async(pending))
        return await self._request(request, retry=retry)

    async def _request(self, request: Request[ResponseT], *, retry: RetryPolicy | None = None) -> ResponseT:
        return await send_async(self._http_client, self._retry, request, retry)

    async def aclose(self) -> None:
        """Release network resources and close the underlying HTTP client, including a supplied one."""
        await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        """Enter the asynchronous client context."""
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the asynchronous client context."""
        await self.aclose()
