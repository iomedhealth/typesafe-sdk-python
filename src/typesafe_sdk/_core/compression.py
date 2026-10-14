"""Request body compression."""

import sys
from collections.abc import Callable
from typing import Literal

from typesafe_sdk._core.errors import TypeSafeError

Compression = Literal["zstd", False]
Encoding = Literal["zstd"]

# Bodies below this size fit in a single TCP segment, so compressing them saves no round trips and only costs CPU.
MIN_COMPRESSED_BODY_LENGTH = 1024

COMPRESSORS: dict[Encoding, Callable[[bytes], bytes]] = {}

try:
    if sys.version_info >= (3, 14):
        from compression.zstd import compress as zstd_compress
    else:
        from backports.zstd import compress as zstd_compress

    COMPRESSORS["zstd"] = zstd_compress
except ImportError:  # Without the `zstd` extra before Python 3.14, or a Python built without zstd.
    pass


def resolve_compression(compression: Compression | None) -> Encoding | None:
    """Resolve the `compression` client option to the `Content-Encoding` to send request bodies with."""
    if compression is None:
        return "zstd" if "zstd" in COMPRESSORS else None
    if compression is False:
        return None
    if compression != "zstd":
        raise TypeSafeError("compression must be 'zstd', False, or None.")
    if "zstd" not in COMPRESSORS:
        raise TypeSafeError("zstd compression requires Python 3.14+ or the `typesafe-sdk[zstd]` extra.")
    return compression
