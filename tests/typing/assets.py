from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import PIL.Image
from pydantic import BaseModel
from typing_extensions import Buffer, assert_type

from typesafe_sdk import Asset, AsyncTypeSafeClient, Choice, Content, Image, Noul, Score, SystemOneResponse, TypeSafeClient


class CustomResponse(BaseModel):
    model: str


def sync(client: TypeSafeClient, image: Buffer) -> None:
    assets: Mapping[str, Image] = MappingProxyType({"<photo>": Image(image)})
    questions = {"q": Noul(instructions="Is it damaged?")}
    assert_type(client.system_one("<photo>", questions, assets=assets), SystemOneResponse)
    assert_type(client.system_one("<photo>", questions, assets=assets, response_model=CustomResponse), CustomResponse)
    client.system_one("<photo>", questions, assets={"<photo>": Image(b"image")})
    client.system_one("<photo>", questions, assets={"<photo>": Image(bytearray(b"image"))})
    client.system_one("<photo>", questions, assets={"<photo>": Image(memoryview(b"image"))})
    client.system_one("<photo>", questions, assets={"<photo>": Image.from_base64("aW1hZ2U=")})
    client.system_one("<photo>", questions, assets={"<photo>": Image.from_base64(b"aW1hZ2U=")})
    client.system_one("<photo>", questions, assets={"<photo>": Image(Path("photo.png"))})
    assert_type(Image.from_pil(PIL.Image.new("RGB", (1, 1)), format="jpeg"), Image)


async def asynchronous(client: AsyncTypeSafeClient, image: Buffer) -> None:
    assets: Mapping[str, Asset] = {"<photo>": Image(image)}
    questions = {"q": Noul(instructions="Is it damaged?")}
    assert_type(await client.system_one("<photo>", questions, assets=assets), SystemOneResponse)
    assert_type(await client.system_one("<photo>", questions, assets=assets, response_model=CustomResponse), CustomResponse)
    await client.system_one("<photo>", questions, assets={"<photo>": Image(b"image")})
    await client.system_one("<photo>", questions, assets={"<photo>": Image(bytearray(b"image"))})
    await client.system_one("<photo>", questions, assets={"<photo>": Image(memoryview(b"image"))})


def inline(client: TypeSafeClient, photo: Image) -> None:
    state: Content = [{"role": "user", "msg": ["Here is what came back:", photo]}]
    assert_type(client.system_one(state, {"q": Noul(instructions=["Same item?", photo])}), SystemOneResponse)
    client.system_one(photo, {"q": Choice(criteria={"match": photo, "other": None})})
    client.system_one("x", {"q": Score(criteria=[photo, "text"])})
    client.system_one("x", {"q": {"type": "noul", "instructions": photo, "criteria": {"true": photo}}})
    with client.attach_assets(photo):
        client.system_one(f"Compare {photo} with the listing.", {"q": Noul(instructions="Is it damaged?")})
    with client.attach_assets((photo, photo)):
        client.system_one(f"Compare {photo} with the listing.", {"q": Noul(instructions="Is it damaged?")})


async def inline_async(client: AsyncTypeSafeClient, photo: Image) -> None:
    with client.attach_assets([photo]):
        await client.system_one(f"Compare {photo} with the listing.", {"q": Noul(instructions="Is it damaged?")})
