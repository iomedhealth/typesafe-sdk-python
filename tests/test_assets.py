import base64
import threading
from array import array
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import httpx2
import PIL.Image
import pytest
from pydantic_core import from_json
from typing_extensions import Buffer

from tests.conftest import ClientFactory
from tests.helpers import multipart_parts, request_body, system_one
from typesafe_sdk import AsyncTypeSafeClient, Choice, Image, Noul, RetryPolicy, Score, TypeSafeError
from typesafe_sdk._core.assets import ResolvedImage

# Only format identification happens in the SDK; the API validates image contents.
PNG = b"\x89PNG\r\n\x1a\nimage data"
JPEG = b"\xff\xd8\xffimage data"


def strided_buffer(data: bytes) -> memoryview:
    interleaved = bytearray(len(data) * 2)
    interleaved[::2] = data
    return memoryview(interleaved)[::2]


def shaped_buffer(data: bytes) -> memoryview:
    return memoryview(data).cast("B", shape=(1, len(data)))


def native_buffer(data: bytes) -> Buffer:
    return array("B", data)


def crlf_terminated_base64(data: bytes) -> bytes:
    return base64.b64encode(data) + b"\r\n"


@pytest.mark.parametrize("data,content_type", [(PNG, "image/png"), (JPEG, "image/jpeg")])
@pytest.mark.parametrize("buffer", [bytes, bytearray, memoryview, strided_buffer, shaped_buffer, native_buffer])
async def test_image_buffers(clients: ClientFactory, data: bytes, content_type: str, buffer: Callable[[bytes], Buffer]) -> None:
    value = buffer(data)
    assets = MappingProxyType({"<product>": Image(value)})
    state = {"photos": ["<product>", "Compare <product> with the description."]}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = request_body(request)
        assert body["state"] == state
        assert body["assets"] == {"<product>": {"type": "image", "content_type": content_type, "data": data}}
        assert body["questions"] == {"q": {"type": "noul", "instructions": "Is it damaged?"}}
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state=state, questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)
    assert assets["<product>"].data is value
    assert memoryview(value).tobytes() == data


@pytest.mark.parametrize("assets", [None, {}])
async def test_no_image_assets(clients: ClientFactory, assets: dict[str, Image] | None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["content-type"] == "application/json"
        # The API rejects an empty `assets`, so it's left out.
        assert "assets" not in from_json(request.content)
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state="text", questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)


async def test_assets_in_question_content(clients: ClientFactory) -> None:
    questions = {
        "n": Noul(instructions={"image": "<png>"}, criteria={"true": ["Like <jpeg>"]}),
        "c": Choice(criteria={"match": "<png>", "other": None}),
        "s": Score(criteria=["<jpeg>"]),
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = request_body(request)
        assert body["questions"] == {name: question.model_dump() for name, question in questions.items()}
        assert list(body["assets"]) == ["<png>", "<jpeg>"]
        assert body["assets"]["<png>"]["data"] == PNG
        assert body["assets"]["<jpeg>"]["data"] == JPEG
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state="Compare the images.", questions=questions, assets={"<png>": Image(PNG), "<jpeg>": Image(JPEG)})


@pytest.mark.parametrize(
    "assets,match",
    [
        ({"": Image(PNG)}, "non-empty string"),
        ({"  ": Image(PNG)}, "non-empty string"),
        ({1: Image(PNG)}, "non-empty string"),
        ({"<photo>": PNG}, "must be an asset"),
        ({"<photo>": {"type": "image", "data": PNG}}, "must be an asset"),
        ({"<photo>": Image(b"")}, "empty"),
        ({"<photo>": Image(cast(Any, "photo.png"))}, "str data"),
        ({"<photo>": Image(Path("missing.png"))}, "Asset '<photo>': Image file 'missing.png' could not be read"),
        ({"<photo>": Image(Path())}, "Image file '.' could not be read"),
        ({"<photo>": Image(cast(Any, BytesIO(PNG)))}, "buffer"),
        ({"<photo>": Image(cast(Any, iter([PNG])))}, "buffer"),
    ],
)
async def test_invalid_assets_before_network(clients: ClientFactory, assets: Any, match: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid assets reached the network")

    with pytest.raises(TypeSafeError, match=match):
        await system_one(clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)


async def test_released_buffer(clients: ClientFactory) -> None:
    data = memoryview(PNG)
    data.release()

    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("A released buffer reached the network")

    with pytest.raises(TypeSafeError, match="buffer"):
        await system_one(
            clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": Image(data)}
        )


@pytest.mark.parametrize("image", [Image(b"invalid"), Image(Path("missing.png"))])
@pytest.mark.parametrize("override", [None, {}, {"<photo>": {"type": "image", "format": "png", "data_base64": "raw-override"}}])
async def test_extra_body_overrides_assets(clients: ClientFactory, override: Any, image: Image) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert from_json(request.content)["assets"] == override
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    # Overridden inputs are not read, encoded, or validated.
    await system_one(
        clients(handler),
        state="<photo>",
        questions={"q": Noul(instructions="Is it damaged?")},
        assets={"<photo>": image},
        extra_body={"assets": override},
    )


@pytest.mark.parametrize("data,content_type", [(PNG, "image/png"), (JPEG, "image/jpeg")])
async def test_image_paths(clients: ClientFactory, tmp_path: Path, data: bytes, content_type: str) -> None:
    path = tmp_path / "photo"
    image = Image(path)
    # The file is read when the request is prepared, not when the Image is created.
    path.write_bytes(data)

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request_body(request)["assets"] == {"<photo>": {"type": "image", "content_type": content_type, "data": data}}
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": image})


async def test_async_client_reads_paths_off_the_event_loop(clients: ClientFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    threads: list[threading.Thread] = []
    read_bytes = Path.read_bytes

    def tracked_read_bytes(self: Path) -> bytes:
        threads.append(threading.current_thread())
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    client = clients(handler)
    await system_one(client, state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": Image(path)})
    # The async client reads through anyio's worker threads; the sync client reads in place.
    assert len(threads) == 1
    assert (threads[0] is threading.current_thread()) is not isinstance(client, AsyncTypeSafeClient)


async def test_first_failing_alias_is_reported(clients: ClientFactory) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid assets reached the network")

    # The async client resolves assets concurrently but still names the first failing alias in mapping order.
    assets = {"<ok>": Image(PNG), "<missing>": Image(Path("missing.png")), "<empty>": Image(b"")}
    with pytest.raises(TypeSafeError, match=r"^Asset '<missing>': Image file 'missing.png' could not be read"):
        await system_one(clients(handler), state="<ok> <missing> <empty>", questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)


async def test_unexpected_errors_raise_unwrapped(clients: ClientFactory) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid assets reached the network")

    # Both clients raise the first failing alias's error as is, not in an ExceptionGroup.
    assets = {"<ok>": Image(PNG), "<null>": Image(Path("null\0byte.png")), "<missing>": Image(Path("missing.png"))}
    with pytest.raises(ValueError, match="null byte"):
        await system_one(clients(handler), state="<ok>", questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)


def test_images_compare_by_identity() -> None:
    # Equal contents don't make two images the same asset, and images with mutable buffers are hashable too.
    first, second = Image(bytearray(PNG)), Image(bytearray(PNG))
    assert first != second
    assert len({first, second, Image(memoryview(bytearray(PNG)))}) == 3


async def test_resolve_returns_wire_form(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    image = Image(path)
    expected = ResolvedImage(content_type="image/png", data=memoryview(PNG))
    assert image._resolve() == expected
    assert await image._resolve_async() == expected
    # Errors from resolving one image don't name an alias; clients add it.
    with pytest.raises(TypeSafeError, match=r"^Image data is empty\.$"):
        Image(b"")._resolve()


@pytest.mark.parametrize("sync_first", [True, False])
async def test_images_are_resolved_once(tmp_path: Path, sync_first: bool) -> None:
    data = bytearray(PNG)
    path = tmp_path / "photo.png"
    path.write_bytes(PNG)
    for image in [Image(data), Image(path)]:
        assert not image._is_resolved
        with pytest.raises(RuntimeError, match=r"^Image is not resolved yet"):
            _ = image._resolved
        resolved = image._resolve() if sync_first else await image._resolve_async()
        assert image._is_resolved
        assert image._resolved is resolved
        data[:] = JPEG
        path.write_bytes(JPEG)
        # Later requests reuse the first resolution, even after the data changes.
        assert image._resolve() is resolved
        assert await image._resolve_async() is resolved
        assert resolved.content_type == "image/png"
        data[:] = PNG
        path.write_bytes(PNG)


@pytest.mark.parametrize("data,content_type", [(PNG, "image/png"), (JPEG, "image/jpeg")])
@pytest.mark.parametrize("text", [str, bytes, bytearray, memoryview])
@pytest.mark.parametrize("encode", [base64.b64encode, base64.encodebytes, crlf_terminated_base64])
async def test_from_base64(
    clients: ClientFactory, data: bytes, content_type: str, text: Callable[[str], str | Buffer], encode: Callable[[bytes], bytes]
) -> None:
    # Whitespace, such as the line breaks of wrapped base64, is ignored.
    data_base64 = encode(data * 20).decode("ascii")
    data *= 20
    value = text(data_base64) if text is str else text(data_base64.encode("ascii"))  # pyrefly: ignore[bad-argument-type]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request_body(request)["assets"] == {"<photo>": {"type": "image", "content_type": content_type, "data": data}}
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    image = Image.from_base64(value)
    assert image.data == data
    await system_one(clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": image})


async def test_asset_payload_is_reused_on_retry(clients: ClientFactory) -> None:
    data = bytearray(PNG)
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            data[:] = JPEG
            return httpx2.Response(500, json={"message": "try again"})
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(
        clients(handler),
        state="<photo>",
        questions={"q": Noul(instructions="Is it damaged?")},
        assets={"<photo>": Image(data)},
        retry=RetryPolicy(max_retries=1, backoff_initial=0, backoff_max=0),
    )
    assert len(requests) == 2
    assert requests[0].content == requests[1].content
    assert request_body(requests[0])["assets"]["<photo>"]["data"] == PNG


async def test_multipart_upload(clients: ClientFactory) -> None:
    image = Image(bytearray(PNG))

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        parts = multipart_parts(request)
        assert list(parts) == ["request", "asset-0", "asset-1", "asset-2"]
        head, content = parts["request"]
        assert b"Content-Type: application/json" in head
        # The API requires each part to be claimed by exactly one asset, so an image under several keys has a part per key.
        assert from_json(content)["assets"] == {
            "<a>": {"type": "image", "part": "asset-0"},
            "<b>": {"type": "image", "part": "asset-1"},
            "<c>": {"type": "image", "part": "asset-2"},
        }
        assert parts["asset-0"] == (b'Content-Disposition: form-data; name="asset-0"; filename="asset-0"\r\nContent-Type: image/png', PNG)
        assert parts["asset-1"] == (b'Content-Disposition: form-data; name="asset-1"; filename="asset-1"\r\nContent-Type: image/png', PNG)
        assert parts["asset-2"] == (
            b'Content-Disposition: form-data; name="asset-2"; filename="asset-2"\r\nContent-Type: image/jpeg',
            JPEG,
        )
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    assets = {"<a>": image, "<b>": image, "<c>": Image.from_base64(base64.b64encode(JPEG))}
    await system_one(clients(handler), state="<a> <b> <c>", questions={"q": Noul(instructions="Is it damaged?")}, assets=assets)


@pytest.mark.parametrize(
    "data,match",
    [
        ("", "non-empty ASCII base64"),
        (" \n", "non-empty ASCII base64"),
        ("iVBO\xa0Rw0K", "non-empty ASCII base64"),
        ("iVBORw0KGgoé", "non-empty ASCII base64"),
        (PNG, "non-empty ASCII base64"),
        (cast(Any, 1), "buffer"),
        ("!!!!" + base64.b64encode(PNG).decode(), "valid base64"),
        ("iVBORw0", "valid base64"),
    ],
)
def test_from_base64_rejects_invalid_text(data: str | Buffer, match: str) -> None:
    # Base64 text is decoded immediately, so invalid text fails before any request.
    with pytest.raises(TypeSafeError, match=match):
        Image.from_base64(data)


@pytest.mark.parametrize("image_format,content_type", [("png", "image/png"), ("jpeg", "image/jpeg"), ("gif", "application/octet-stream")])
async def test_from_pil(clients: ClientFactory, image_format: str, content_type: str) -> None:
    image = Image.from_pil(PIL.Image.new("RGB", (4, 4), "red"), format=image_format)

    def handler(request: httpx2.Request) -> httpx2.Response:
        asset = request_body(request)["assets"]["<photo>"]
        assert asset["content_type"] == content_type
        decoded = PIL.Image.open(BytesIO(asset["data"]))
        assert (decoded.format, decoded.size) == (image_format.upper(), (4, 4))
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": image})


def test_from_pil_defaults_to_jpeg_quality_95() -> None:
    pil_image = PIL.Image.new("RGB", (4, 4), "red")
    expected = BytesIO()
    pil_image.save(expected, format="jpeg", quality=95)
    assert Image.from_pil(pil_image).data == expected.getvalue()


def test_from_pil_errors() -> None:
    with pytest.raises(TypeSafeError, match="could not be encoded as jpeg"):
        Image.from_pil(PIL.Image.new("RGBA", (4, 4)), format="jpeg")
    with pytest.raises(TypeSafeError, match="could not be encoded as nope"):
        Image.from_pil(PIL.Image.new("RGB", (4, 4)), format="nope")


async def test_unrecognized_formats_are_sent_as_binary(clients: ClientFactory) -> None:
    # The SDK labels the parts it recognizes and leaves format support to the API.
    gif = b"GIF89a image data"

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request_body(request)["assets"] == {"<photo>": {"type": "image", "content_type": "application/octet-stream", "data": gif}}
        return httpx2.Response(200, json={"model": "vision", "usage": {}, "answers": {}})

    await system_one(clients(handler), state="<photo>", questions={"q": Noul(instructions="Is it damaged?")}, assets={"<photo>": Image(gif)})


def test_image_format_cannot_be_supplied() -> None:
    with pytest.raises(TypeError, match="format"):
        cast(Any, Image)(PNG, format="png")
    with pytest.raises(TypeError):
        cast(Any, Image)(PNG, "png")
