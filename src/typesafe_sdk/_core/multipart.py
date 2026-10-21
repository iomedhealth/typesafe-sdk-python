"""`multipart/form-data` request bodies (RFC 7578)."""

from collections.abc import Sequence
from typing import NamedTuple
from uuid import uuid4

from typing_extensions import Buffer


class Part(NamedTuple):
    """A body part; `name` and `filename` must be ASCII without quotes, as they are written unescaped."""

    name: str
    content_type: str
    content: Buffer
    filename: str | None = None


def encode_multipart(parts: Sequence[Part]) -> tuple[str, bytes]:
    """Encode `parts` into a body, returning its content type and bytes."""
    # A random boundary, like browsers and httpx use, is practically certain not to occur in the content.
    boundary = uuid4().hex
    chunks: list[Buffer] = []
    for name, content_type, content, filename in parts:
        disposition = f'form-data; name="{name}"' + ("" if filename is None else f'; filename="{filename}"')
        chunks += (f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: {content_type}\r\n\r\n".encode(), content, b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return f"multipart/form-data; boundary={boundary}", b"".join(chunks)
