"""Live image tests, run only when `TYPESAFE_LIVE_IMAGES` is set, against an API that accepts images."""

import base64
import os
import struct
import zlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image as PILImage

from tests.conftest import Client
from tests.helpers import system_one
from typesafe_sdk import AsyncTypeSafeClient, Box, Choice, Image, Noul, Score, TypeSafeClient

pytestmark = pytest.mark.integration


def png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


# The API rejects images below a minimum size, so test images are comfortably above it.
SIZE = 512
RED = png(SIZE, SIZE, (255, 0, 0))
BLUE = png(SIZE, SIZE, (0, 0, 255))
# The API rejects image requests that don't opt into the experimental feature.
IMAGE_HEADERS = {"TypeSafe-Danger-Zone-Images": "true"}


@pytest.fixture(params=["sync", "async"])
async def image_client(request: pytest.FixtureRequest, live_api_key: str, live_compression: Literal[False] | None) -> AsyncIterator[Client]:
    if not os.environ.get("TYPESAFE_LIVE_IMAGES"):
        pytest.skip("TYPESAFE_LIVE_IMAGES is not set")
    if request.param == "async":
        async with AsyncTypeSafeClient(api_key=live_api_key, timeout=120, headers=IMAGE_HEADERS, compression=live_compression) as client:
            yield client
    else:
        with TypeSafeClient(api_key=live_api_key, timeout=120, headers=IMAGE_HEADERS, compression=live_compression) as client:
            yield client


@pytest.mark.parametrize("image", [Image(RED), Image.from_pil(PILImage.new("RGB", (SIZE, SIZE), "red"), format="jpeg")], ids=["png", "jpeg"])
async def test_live_inline_image(image_client: Client, image: Image) -> None:
    result = await system_one(
        image_client,
        state=["Here is a photo:", image],
        questions={"red": Noul(instructions="Is the photo a solid red square?")},
    )
    assert result.nouls["red"].noul > 0.5


async def test_live_image_under_alias_and_reference(image_client: Client) -> None:
    # Sent under two asset keys, so each key needs its own part.
    photo = Image(RED)
    result = await system_one(
        image_client,
        state=f"Compare <photo> with {photo}.",
        questions={"same": Noul(instructions="Are both photos the same?")},
        assets={"<photo>": photo},
    )
    assert result.nouls["same"].noul > 0.5


async def test_live_image_in_question(image_client: Client) -> None:
    photo = Image.from_base64(base64.b64encode(RED))
    result = await system_one(
        image_client,
        state="Look at the image in the question.",
        questions={"red": Noul(instructions=["Is this image a solid red square?", photo])},
    )
    assert result.nouls["red"].noul > 0.5


async def test_live_images_in_question_criteria(image_client: Client) -> None:
    result = await system_one(
        image_client,
        state=Image(RED),
        questions={
            "color": Choice(
                instructions="Which sample has the same color as the state image?",
                criteria={"red": Image(RED), "blue": Image(BLUE)},
            ),
            "similarity": Score(
                instructions="How similar is the state image to the examples?",
                criteria=[Image(BLUE), Image(RED)],
            ),
        },
    )
    assert result.choices["color"].choice == "red"
    assert result.scores["similarity"].score > 0.5


async def test_live_attached_image_path(image_client: Client, tmp_path: Path) -> None:
    path = tmp_path / "red.png"
    path.write_bytes(RED)
    photo = Image(path)
    with image_client.attach_assets(photo):
        result = await system_one(
            image_client,
            state=f"Here is a photo: {photo}",
            questions={"red": Noul(instructions="Is the photo a solid red square?")},
        )
    assert result.nouls["red"].noul > 0.5


async def test_live_box(image_client: Client) -> None:
    photo = PILImage.new("RGB", (SIZE, SIZE), "white")
    photo.paste("red", (SIZE // 4, SIZE // 4, SIZE * 3 // 4, SIZE * 3 // 4))
    result = await system_one(
        image_client,
        state=Image.from_pil(photo),
        questions={"red_square": Box(instructions="The red square in the center")},
    )
    box = result.boxes["red_square"]
    assert box is result.answers["red_square"]
    edges = box.box
    assert all(0 <= edge <= 1 for edge in (edges.top, edges.bottom, edges.left, edges.right))
    assert box.confidence is None or 0 <= box.confidence <= 1
