"""Question validation before JSON encoding."""

from collections.abc import Mapping, Sequence

from typesafe_sdk._core.content_types import Content
from typesafe_sdk._core.errors import TypeSafeError
from typesafe_sdk._core.question_types import Box, Choice, Noul, Question, Score


def normalize_questions(questions: Mapping[str, Question]) -> dict[str, Question]:
    if not questions:
        raise TypeSafeError("At least one question is required.")
    for name, question in questions.items():
        if isinstance(question, Choice):
            _validate_choice_criteria(name, question.criteria)
        elif isinstance(question, Noul):
            _validate_noul(name, question.instructions, question.criteria)
        elif not isinstance(question, (Score, Box)):
            if not isinstance(question, dict) or not isinstance(question.get("type"), str) or not question["type"]:
                raise TypeSafeError(f'Question "{name}" must be a question object or a dictionary with a nonempty string "type".')
            if question["type"] in ("choice", "score") and "criteria" not in question:
                raise TypeSafeError(f'Question "{name}" requires "criteria".')
            if question["type"] == "score":
                _validate_score_criteria(name, question["criteria"])
            elif question["type"] == "choice":
                _validate_choice_criteria(name, question["criteria"])
            elif question["type"] == "noul":
                _validate_noul(name, question.get("instructions"), question.get("criteria"))
    return dict(questions)


def _validate_score_criteria(name: str, criteria: Sequence[Content]) -> None:
    """Reject a score question with no criteria; at least one score is required."""
    if not criteria:
        raise TypeSafeError(f'Score question "{name}" has no criteria; at least one score is required.')


def _validate_choice_criteria(name: str, criteria: Mapping[str, Content | None]) -> None:
    """Reject a choice question with no criteria; at least one choice is required."""
    if not criteria:
        raise TypeSafeError(f'Choice question "{name}" has no criteria; at least one choice is required.')


def _validate_noul(name: str, instructions: object, criteria: object) -> None:
    """Reject a noul question with non-object criteria or with neither instructions nor a described outcome."""
    if criteria is not None and not isinstance(criteria, Mapping):
        raise TypeSafeError(f'Noul question "{name}" criteria must be an object.')
    if instructions is None and not (isinstance(criteria, Mapping) and any(value is not None for value in criteria.values())):
        raise TypeSafeError(f'Noul question "{name}" has neither instructions nor criteria; at least one is required.')
