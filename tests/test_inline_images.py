import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import httpx2
import pytest

from tests.conftest import ClientFactory
from tests.helpers import request_body, system_one
from typesafe_sdk import Choice, Content, Image, Noul, NoulModel, Questions, Score, SystemOneResponse, TypeSafeClient, TypeSafeError

PNG = b"\x89PNG\r\n\x1a\nimage data"
JPEG = b"\xff\xd8\xffimage data"
OK: dict[str, object] = {"model": "vision", "usage": {}, "answers": {}}


def capture(clients: ClientFactory, bodies: list[Any]) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(request_body(request))
        return httpx2.Response(200, json=OK)

    return clients(handler)


def asset_data(body: Any, alias: str) -> bytes:
    return body["assets"][alias]["data"]


def ref(image: Image) -> str:
    return image._reference


def test_reference() -> None:
    image = Image(PNG)
    assert re.fullmatch(r"<image:[0-9a-f]{32}>", ref(image))
    assert ref(image) != ref(Image(PNG))
    assert str(image) == f"{image}" == ref(image)
    # Images compare by identity, not by value.
    assert image != Image(PNG)
    assert "<image:" not in repr(image)


async def test_images_in_state_and_questions(clients: ClientFactory) -> None:
    returned, listing = Image(PNG), Image(JPEG)
    state = [{"role": "user", "msg": ["Here is what came back:", returned]}, ("listing", listing)]
    raw: NoulModel = {"type": "noul", "instructions": returned, "criteria": {"true": listing}}
    questions: Questions = {
        "same": Noul(instructions=["Is it the item in this photo?", listing]),
        "condition": Choice(criteria={"new": returned, "used": None}),
        "damage": Score(criteria=["none", {"example": [returned]}]),
        "raw": raw,
    }
    bodies: list[Any] = []
    await system_one(capture(clients, bodies), state=state, questions=questions)
    (body,) = bodies
    r, li = ref(returned), ref(listing)
    # Each image is sent once, under its reference wherever it appears.
    assert set(body["assets"]) == {r, li}
    assert asset_data(body, r) == PNG
    assert asset_data(body, li) == JPEG
    assert body["state"] == [{"role": "user", "msg": ["Here is what came back:", r]}, ["listing", li]]
    assert body["questions"] == {
        "same": {"type": "noul", "instructions": ["Is it the item in this photo?", li]},
        "condition": {"type": "choice", "criteria": {"new": r, "used": None}},
        "damage": {"type": "score", "criteria": ["none", {"example": [r]}]},
        "raw": {"type": "noul", "instructions": r, "criteria": {"true": li}},
    }


async def test_image_in_custom_mapping(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    await system_one(capture(clients, bodies), state=MappingProxyType({"photo": image}), questions={"q": Noul(instructions="Is it damaged?")})
    (body,) = bodies
    assert body["state"] == {"photo": ref(image)}
    assert asset_data(body, ref(image)) == PNG


async def test_image_as_whole_state(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    await system_one(capture(clients, bodies), state=image, questions={"q": Noul(instructions="Is it damaged?")})
    (body,) = bodies
    assert body["state"] == ref(image)
    assert asset_data(body, ref(image)) == PNG


@pytest.mark.parametrize("state", ["text", MappingProxyType({"a": [1, "x", None, (True, 0.5)]})])
async def test_content_without_images_is_unchanged(clients: ClientFactory, state: Content) -> None:
    bodies: list[Any] = []
    await system_one(capture(clients, bodies), state=state, questions={"q": Noul(instructions={"text": ["a", "b"]})})
    (body,) = bodies
    assert "assets" not in body
    assert body["questions"] == {"q": {"type": "noul", "instructions": {"text": ["a", "b"]}}}


async def test_aliased_and_inline_images_combine(clients: ClientFactory) -> None:
    inline = Image(JPEG)
    bodies: list[Any] = []
    await system_one(
        capture(clients, bodies),
        state=["Compare with <listing>.", inline],
        questions={"q": Noul(instructions="Is it damaged?")},
        assets={"<listing>": Image(PNG)},
    )
    (body,) = bodies
    assert list(body["assets"]) == ["<listing>", ref(inline)]
    assert asset_data(body, "<listing>") == PNG
    assert asset_data(body, ref(inline)) == JPEG


async def test_inline_image_errors_name_the_reference(clients: ClientFactory) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Invalid images reached the network")

    image = Image(Path("missing.png"))
    with pytest.raises(TypeSafeError, match=rf"^Asset '{ref(image)}': Image file 'missing.png' could not be read"):
        await system_one(clients(handler), state={"photos": [image]}, questions={"q": Noul(instructions="Is it damaged?")})


async def test_extra_body_overrides_inline_assets(clients: ClientFactory) -> None:
    image = Image(Path("missing.png"))
    bodies: list[Any] = []
    # Overridden assets are not read, so the missing file raises no error.
    await system_one(capture(clients, bodies), state=image, questions={"q": Noul(instructions="Is it damaged?")}, extra_body={"assets": {}})
    (body,) = bodies
    assert body["assets"] == {}
    assert body["state"] == ref(image)


async def test_named_image_in_content_is_sent_once_under_its_alias(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    await system_one(
        capture(clients, bodies),
        state=["Compare <photo> with", image],
        questions={"q": Noul(instructions=["Is it damaged?", image])},
        assets={"<photo>": image},
    )
    (body,) = bodies
    assert list(body["assets"]) == ["<photo>"]
    assert body["state"] == ["Compare <photo> with", "<photo>"]
    assert body["questions"]["q"]["instructions"] == ["Is it damaged?", "<photo>"]


NOUL: Questions = {"q": Noul(instructions="Is it damaged?")}


async def test_images_in_f_strings(clients: ClientFactory) -> None:
    returned, listing = Image(PNG), Image(JPEG)
    bodies: list[Any] = []
    client = capture(clients, bodies)
    with client.attach_assets([returned, listing]):
        await system_one(
            client,
            state=f"The customer says {returned} arrived scratched.",
            questions={"same": Noul(instructions=f"Is {returned} the item in {listing}?")},
        )
    (body,) = bodies
    assert body["state"] == f"The customer says {returned} arrived scratched."
    assert body["questions"]["same"]["instructions"] == f"Is {returned} the item in {listing}?"
    assert list(body["assets"]) == [str(returned), str(listing)]
    assert asset_data(body, str(listing)) == JPEG


async def test_only_referenced_attached_images_are_sent(clients: ClientFactory) -> None:
    first, second = Image(PNG), Image(JPEG)
    bodies: list[Any] = []
    client = capture(clients, bodies)
    with client.attach_assets([first, second]):
        await system_one(client, state=f"Look at {first}", questions=NOUL)
        await system_one(client, state="No images here.", questions=NOUL)
    assert list(bodies[0]["assets"]) == [str(first)]
    assert "assets" not in bodies[1]


async def test_attachments_nest_and_end_with_the_block(clients: ClientFactory) -> None:
    outer, inner = Image(PNG), Image(JPEG)
    bodies: list[Any] = []
    client = capture(clients, bodies)
    with client.attach_assets(outer):
        with client.attach_assets(inner):
            await system_one(client, state=f"{outer} {inner}", questions=NOUL)
        await system_one(client, state=f"{outer} {inner}", questions=NOUL)
    await system_one(client, state=f"{outer} {inner}", questions=NOUL)
    assert [list(body.get("assets", [])) for body in bodies] == [[str(outer), str(inner)], [str(outer)], []]


async def test_attachments_are_scoped_to_the_client(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    attaching, other = capture(clients, bodies), capture(clients, bodies)
    with attaching.attach_assets(image):
        await system_one(other, state=f"{image}", questions=NOUL)
    (body,) = bodies
    assert "assets" not in body


def test_attachments_reach_worker_threads() -> None:
    images = [Image(PNG) for _ in range(8)]
    bodies: list[Any] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(request_body(request))
        return httpx2.Response(200, json=OK)

    with (
        TypeSafeClient(api_key="test", transport=httpx2.MockTransport(handler)) as client,
        client.attach_assets(images),
        ThreadPoolExecutor(4) as pool,
    ):

        def send(image: Image) -> SystemOneResponse:
            return client.system_one(f"{image}", NOUL)

        list(pool.map(send, images))
    assert sorted(next(iter(body["assets"])) for body in bodies) == sorted(map(str, images))


async def test_overlapping_attachments_hold_the_image_until_the_last_exits(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    client = capture(clients, bodies)
    first, second = client.attach_assets(image), client.attach_assets([image])
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    await system_one(client, state=f"{image}", questions=NOUL)
    second.__exit__(None, None, None)
    await system_one(client, state=f"{image}", questions=NOUL)
    assert [list(body.get("assets", [])) for body in bodies] == [[str(image)], []]
    # Released assets are no longer referenced by the client.
    assert not client._attached._assets


async def test_formatted_image_found_in_content(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    # An image placed in the content needs no attachment for other strings to reference it.
    await system_one(capture(clients, bodies), state=[image, f"Compare {image} with the listing."], questions=NOUL)
    (body,) = bodies
    assert list(body["assets"]) == [str(image)]


async def test_named_image_formatted_into_text_needs_no_attachment(clients: ClientFactory) -> None:
    image = Image(Path("missing.png"))
    bodies: list[Any] = []
    client = capture(clients, bodies)
    # The text holds the literal reference, so the image is sent under it as well as its alias, but read once.
    with pytest.raises(TypeSafeError, match=r"^Asset '<photo>': Image file 'missing.png' could not be read"):
        await system_one(client, state=f"<photo> is {image}", questions=NOUL, assets={"<photo>": image})
    image = Image(PNG)
    await system_one(client, state=f"<photo> is {image}", questions=NOUL, assets={"<photo>": image})
    (body,) = bodies
    assert list(body["assets"]) == ["<photo>", str(image)]
    assert asset_data(body, str(image)) == PNG


async def test_unknown_references_are_sent_as_text(clients: ClientFactory) -> None:
    image = Image(PNG)
    bodies: list[Any] = []
    # Only known references are matched: text that merely looks like one is left alone.
    state = f"Look at {image} and <image:{'0' * 32}>"
    await system_one(capture(clients, bodies), state=state, questions=NOUL, assets={"<photo>": Image(PNG)})
    (body,) = bodies
    assert body["state"] == state
    assert list(body["assets"]) == ["<photo>"]


def test_invalid_attached_asset(clients: ClientFactory) -> None:
    client = clients(lambda request: pytest.fail("unreachable"))
    with pytest.raises(TypeSafeError, match=r"^Attached value b'image' must be an asset\.$"), client.attach_assets(cast(Any, [b"image"])):
        pass
