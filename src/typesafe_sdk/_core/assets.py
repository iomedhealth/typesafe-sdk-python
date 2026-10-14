"""Asset objects and their resolution into API assets."""

import re
from base64 import b64decode
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeAlias
from uuid import uuid4

import anyio
from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, SchemaSerializer, core_schema
from pydantic_core.core_schema import SerializationInfo
from typing_extensions import Buffer, Self

from typesafe_sdk._core.errors import TypeSafeError

if TYPE_CHECKING:
    import PIL.Image

# Content types of the image formats recognized by their file signatures, to label upload parts.
# The API detects formats itself, so unrecognized images are still sent, labeled as generic binary data.
CONTENT_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}
SIGNATURE_LENGTH = max(map(len, CONTENT_TYPES))
UNKNOWN_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class References:
    """Request serialization context: serializes assets as their alias or reference, collecting referenced assets."""

    aliases: Mapping[int, str]
    """Aliases of the assets passed in `assets`, by `id(asset)`."""
    found: dict[str, "Asset"] = field(default_factory=dict)
    """Assets serialized as their reference, by reference."""


# Matches the reference of any image, `str(image)`; the reference is the same in text and in JSON.
REFERENCE_PATTERN = re.compile(rb"<image:[0-9a-f]{32}>")


def _serialize_reference(asset: "Image", info: SerializationInfo) -> str:
    references = info.context
    reference = asset._reference  # noqa: SLF001 - Internal asset protocol.
    if not isinstance(references, References):
        return reference
    alias = references.aliases.get(id(asset))
    if alias is not None:
        return alias
    references.found[reference] = asset
    return reference


REFERENCE_SERIALIZATION = core_schema.plain_serializer_function_ser_schema(_serialize_reference, info_arg=True)


@dataclass(frozen=True)
class ResolvedImage:
    """An image asset with its data read and validated."""

    type: Literal["image"] = field(default="image", init=False)
    """Asset type discriminator, always `"image"`."""
    content_type: str
    """The image's content type, recognized from its file signature."""
    data: memoryview = field(hash=False)
    """The image file bytes."""


@dataclass(frozen=True, eq=False)
class Image:
    """An image asset backed by image file bytes, or a path to an image file.

    !!! tip "Reusing images"
        An `Image` is loaded once, on first use, and reused by every request that sends the same `Image` object.

    !!! tip "Working with `Buffer` objects"
        `Image` also accepts `Buffer` objects: anything exposing its memory through the [buffer protocol](https://docs.python.org/3/c-api/buffer.html), such as `bytearray`,
        `memoryview`, `mmap`, or NumPy arrays.
        Buffers other than `bytes` are copied into memory once, on first use, so the original can change or be closed afterwards.

    Examples:
        From a file path:

        <!-- skip: next -->
        ```python
        from pathlib import Path

        image = Image(Path("photos/product.png"))
        ```

        From bytes in memory:

        ```python
        def from_bytes(data: bytes) -> Image:
            return Image(data)
        ```

        From base64 text, such as an upload:

        ```python
        def from_upload(data_base64: str) -> Image:
            return Image.from_base64(data_base64)
        ```

        From a Pillow image (requires the `pil` extra):

        ```python
        import PIL.Image

        image = Image.from_pil(PIL.Image.new("RGB", (64, 64)), format="jpeg")
        ```

        From a slice of a larger buffer, without copying it into `bytes` first:

        ```python
        def embedded_photo(container: bytes, offset: int, length: int) -> Image:
            return Image(memoryview(container)[offset : offset + length])
        ```

        From a memory-mapped file:

        ```python
        import mmap


        def map_photo(path: Path) -> Image:
            with path.open("rb") as file:
                return Image(mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ))
        ```
    """

    type: Literal["image"] = field(default="image", init=False)
    """Asset type discriminator, always `"image"`."""
    data: Buffer | PathLike[str]
    """Image contents, represented as one of:

        - raw bytes (anything supporting the [`Buffer` protocol](https://docs.python.org/3/library/stdtypes.html#buffer-objects))

        - [path-like][os.PathLike] object pointing at the image on the filesystem
    """
    _reference: str = field(default_factory=lambda: f"<image:{uuid4().hex}>", init=False, repr=False)
    _resolution: ResolvedImage | None = field(default=None, init=False, repr=False)

    # pydantic-core encodes an `Image` as its alias or reference anywhere in a request body, including plain dicts and lists.
    __pydantic_serializer__: ClassVar[SchemaSerializer] = SchemaSerializer(core_schema.any_schema(serialization=REFERENCE_SERIALIZATION))

    @classmethod
    def from_base64(cls, data: str | Buffer) -> Self:
        """Create an `Image` instance from a base64-encoded image bytes.

        Args:
            data: The image file bytes, base64-encoded, as text or an ASCII buffer. Whitespace, such as the
                line breaks of wrapped base64, is ignored.

        Raises:
            TypeSafeError: The data isn't valid base64 text.
        """
        text = data if isinstance(data, str) else _buffer(data).tobytes().decode("latin-1")
        text = "".join(text.split()) if text.isascii() else ""
        if not text:
            raise TypeSafeError("Image data must contain non-empty ASCII base64 text.")
        try:
            return cls(b64decode(text, validate=True))
        except ValueError as error:
            raise TypeSafeError("Image data must contain valid base64 text.") from error

    @classmethod
    def from_pil(cls, image: "PIL.Image.Image", format: str = "jpeg") -> Self:  # noqa: A002 - Pillow's own name for it.
        """Create an `Image` instance from a [Pillow](https://pillow.readthedocs.io/) image.

        !!! warning "Encoder settings"
            The image is saved with Pillow's default encoder settings, except that JPEG uses quality 95.
            JPEG can't store transparency or palettes, so pass `format="png"` for RGBA or P mode images.
            Encode it yourself if different settings are needed.

        Args:
            image: The Pillow image to encode.
            format: The image format to save as, by its [Pillow name](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html).

        Raises:
            TypeSafeError: Pillow can't encode the image in this format.
        """
        buffer = BytesIO()
        try:
            image.save(buffer, format=format, quality=95)
        except (KeyError, OSError, ValueError) as error:
            # Pillow raises `KeyError` for a format it doesn't know.
            raise TypeSafeError(f"Pillow image could not be encoded as {format}: {error}") from error
        return cls(buffer.getvalue())

    def __str__(self) -> str:
        """The unique reference to this image, for using it inside text, such as an f-string."""
        return self._reference

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        # Pydantic models, such as question objects, hold `Image` objects as-is; there is no schema for buffer data.
        return core_schema.is_instance_schema(cls, serialization=REFERENCE_SERIALIZATION)

    @property
    def _is_resolved(self) -> bool:
        """Whether the image has been read and validated."""
        return self._resolution is not None

    @property
    def _resolved(self) -> ResolvedImage:
        """The image in its wire form.

        Raises:
            RuntimeError: The image is not resolved yet; check `_is_resolved` first.
        """
        if self._resolution is None:
            raise RuntimeError("Image is not resolved yet; check `_is_resolved` first.")
        return self._resolution

    def _resolve(self) -> ResolvedImage:
        """Read and validate the image once, reading a path with blocking file I/O.

        Raises:
            TypeSafeError: The data can't be read or is empty.
        """
        if not self._is_resolved:
            data = self.data
            if isinstance(data, PathLike):
                path = data
                try:
                    data = Path(path).read_bytes()
                except OSError as error:
                    raise _read_error(path, error) from error
            self._set_resolved(data)
        return self._resolved

    async def _resolve_async(self) -> ResolvedImage:
        """Read and validate the image once, reading a path with async file I/O.

        Validation runs on the event loop.

        Raises:
            TypeSafeError: The data can't be read or is empty.
        """
        if not self._is_resolved:
            data = self.data
            if isinstance(data, PathLike):
                path = data
                try:
                    data = await anyio.Path(path).read_bytes()
                except OSError as error:
                    raise _read_error(path, error) from error
            self._set_resolved(data)
        return self._resolved

    def _set_resolved(self, data: Buffer) -> None:
        # The frozen dataclass caches its resolution in a private field.
        object.__setattr__(self, "_resolution", self._validate(data))

    def _validate(self, data: Buffer) -> ResolvedImage:
        """Validate the image contents, with any path already read into `data`."""
        if isinstance(data, str):
            raise TypeSafeError("Image has str data; pass image file bytes or a path, or use Image.from_base64 for base64 text.")
        buffer = _buffer(data)
        if not isinstance(buffer.obj, bytes):
            # The cached view would keep a mutable buffer from being resized or closed, so copy it once.
            with buffer:
                buffer = memoryview(buffer.tobytes())
        if not buffer:
            raise TypeSafeError("Image data is empty.")
        return ResolvedImage(content_type=_content_type(buffer[:SIGNATURE_LENGTH]), data=buffer)


Asset: TypeAlias = Image
"""An asset referenced from request content; currently only images are supported."""


def resolve_assets(assets: Mapping[str, Asset]) -> dict[str, ResolvedImage]:
    """Resolve each distinct asset once with blocking file I/O, naming the failing alias in errors."""
    resolved: dict[int, ResolvedImage] = {}
    for name, asset in _assets(assets):
        if id(asset) in resolved:
            continue
        try:
            resolved[id(asset)] = asset._resolve()  # noqa: SLF001 - Internal asset protocol.
        except TypeSafeError as error:
            raise _asset_error(name, error) from error
    return {name: resolved[id(asset)] for name, asset in assets.items()}


async def resolve_assets_async(assets: Mapping[str, Asset]) -> dict[str, ResolvedImage]:
    """Resolve each distinct asset once, concurrently with async file I/O, naming the first failing alias in errors."""
    items = list(_assets(assets))
    results: dict[int, ResolvedImage | Exception] = {}

    async def resolve(asset: Asset) -> None:
        # Errors are collected rather than raised so the task group doesn't wrap them in an ExceptionGroup,
        # and the first failing alias raises as it would with blocking file I/O.
        try:
            results[id(asset)] = await asset._resolve_async()  # noqa: SLF001 - Internal asset protocol.
        except Exception as error:  # noqa: BLE001 - Re-raised below.
            results[id(asset)] = error

    async with anyio.create_task_group() as task_group:
        for asset in {id(asset): asset for _, asset in items}.values():
            task_group.start_soon(resolve, asset)
    resolved = {}
    for name, asset in items:
        result = results[id(asset)]
        if isinstance(result, TypeSafeError):
            raise _asset_error(name, result) from result
        if isinstance(result, Exception):
            raise result
        resolved[name] = result
    return resolved


def _assets(assets: Mapping[str, Asset]) -> Iterator[tuple[str, Asset]]:
    for name, asset in assets.items():
        if not isinstance(name, str) or not name.strip():
            raise TypeSafeError("Asset aliases must be non-empty strings.")
        if not isinstance(asset, Asset):
            raise TypeSafeError(f"Asset {name!r} must be an asset.")
        yield name, asset


def _asset_error(name: str, error: TypeSafeError) -> TypeSafeError:
    return TypeSafeError(f"Asset {name!r}: {error}")


def _read_error(path: PathLike[str], error: OSError) -> TypeSafeError:
    return TypeSafeError(f"Image file {str(path)!r} could not be read: {error.strerror}.")


def _buffer(value: Buffer) -> memoryview:
    try:
        data = memoryview(value)
    except (TypeError, ValueError) as error:
        raise TypeSafeError("Image data must be an accessible buffer of image file bytes.") from error
    # C-extension buffers work through memoryview on Python 3.10/3.11 too,
    # where isinstance(value, typing_extensions.Buffer) cannot recognize all of them.
    # Flatten shaped buffers and copy only when a contiguous view is unavailable.
    return data.cast("B") if data.c_contiguous else memoryview(data.tobytes())


def _content_type(header: bytes | memoryview) -> str:
    for signature, content_type in CONTENT_TYPES.items():
        if header[: len(signature)] == signature:
            return content_type
    return UNKNOWN_CONTENT_TYPE
