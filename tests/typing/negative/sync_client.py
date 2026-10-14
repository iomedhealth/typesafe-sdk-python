import httpx2
from tenacity import AsyncRetrying

from typesafe_sdk import Image, TypeSafeClient


def invalid_arguments(client: TypeSafeClient) -> None:
    client.system_one(None, {})  # E: Argument `None` is not assignable to parameter `state`
    TypeSafeClient(api_key="test", retry=AsyncRetrying())  # E: Argument `AsyncRetrying` is not assignable to parameter `retry`
    TypeSafeClient(api_key="test", http_client=httpx2.AsyncClient())  # E: Argument `AsyncClient` is not assignable to parameter `http_client`
    client.system_one("x", {}, retry=AsyncRetrying())  # E: Argument `AsyncRetrying` is not assignable to parameter `retry`
    client.system_one("x", {}, response_model=int)  # E: No matching overload
    client.system_one("x", {}, response_model=object())  # E: No matching overload
    client.system_one("x", {}, assets={"<photo>": b"image"})  # E: No matching overload
    Image(iter([b"image"]))  # E: is not assignable to parameter `data`
    Image("photo.png")  # E: `Literal['photo.png']` is not assignable to parameter `data`
    Image(b"aGk=", encoding="base64")  # E: Unexpected keyword argument `encoding`
    client.system_one("x", {}, assets={1: Image(b"image")})  # E: No matching overload
    client.system_one("x", {}, assets=[Image(b"image")])  # E: No matching overload
    client.attach_assets(b"image")  # E: is not assignable to parameter `assets`
