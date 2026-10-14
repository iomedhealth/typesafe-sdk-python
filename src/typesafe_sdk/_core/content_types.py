"""Request content types: JSON values that may also hold inline assets.

Defined with `TypeAliasType` for the same reason as the JSON types. Assets come last so that Pydantic
serializes containers as containers, rather than through the reference serializer of an asset.
"""

from collections.abc import Mapping, Sequence

from typing_extensions import TypeAliasType

from typesafe_sdk._core.assets import Asset

ContentValue = TypeAliasType(
    "ContentValue",
    "str | int | float | bool | Sequence[ContentValue | None] | Mapping[str, ContentValue | None] | Asset",
)
"""A JSON-like value that may also hold [assets][typesafe_sdk.Asset]. May be nested and contain `None`."""

Content = TypeAliasType(
    "Content",
    "str | Mapping[str, ContentValue | None] | Sequence[ContentValue | None] | Asset",
)
"""Text, an [asset][typesafe_sdk.Asset], or a mapping/sequence of [`ContentValue`][typesafe_sdk.ContentValue] entries.

Each asset is sent in the request's assets, with its unique reference in its place.
"""
