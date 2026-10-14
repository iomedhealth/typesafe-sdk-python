"""Assets attached to a client, for referencing them inside strings."""

import threading
from collections import Counter
from collections.abc import Generator, Sequence
from contextlib import contextmanager

from typesafe_sdk._core.assets import Asset
from typesafe_sdk._core.errors import TypeSafeError


class AttachedAssets:
    """A client's attached assets, keyed by reference and shared by its calls from any thread or task."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assets: dict[str, Asset] = {}
        # Overlapping blocks may attach the same asset; it stays attached until the last of them exits.
        self._holds: Counter[str] = Counter()

    @contextmanager
    def attach(self, assets: Asset | Sequence[Asset]) -> Generator[None, None, None]:
        """Attach assets for the duration of the block.

        Raises:
            TypeSafeError: An attached value is not an asset.
        """
        attached = [assets] if isinstance(assets, Asset) else list(assets)
        for asset in attached:
            if not isinstance(asset, Asset):
                raise TypeSafeError(f"Attached value {asset!r} must be an asset.")
        with self._lock:
            for asset in attached:
                self._assets[str(asset)] = asset
                self._holds[str(asset)] += 1
        try:
            yield
        finally:
            with self._lock:
                for asset in attached:
                    reference = str(asset)
                    self._holds[reference] -= 1
                    if not self._holds[reference]:
                        del self._holds[reference], self._assets[reference]

    def snapshot(self) -> dict[str, Asset]:
        """The currently attached assets, keyed by reference."""
        with self._lock:
            return dict(self._assets)
