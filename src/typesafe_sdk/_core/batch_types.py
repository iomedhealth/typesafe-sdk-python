"""Input types for the batch System One endpoint.

The batch endpoint (`POST /v1/systemone/batch`) is an extension served by self-hosted System One
deployments; it is not part of the public TypeSafe AI API.
"""

from collections.abc import Mapping

from typing_extensions import NotRequired, TypedDict

from typesafe_sdk._core.json_types import JSONContent
from typesafe_sdk._core.question_types import Question


class SystemOneBatchItem(TypedDict, closed=True):
    """One evaluation of a batch request, with the same fields as a single `system_one` call."""

    state: JSONContent
    """Text, a JSON object, or an array to evaluate."""
    questions: Mapping[str, Question]
    """Nonempty mapping of names to question objects or raw dictionaries."""
    model: NotRequired[str | None]
    """Model override for this item; `None` or omitted inherits the batch-level model, then the client default."""
