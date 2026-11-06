"""Shared schema primitives used by structured-output models."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator


def validate_canonical_dataset_id(value: object) -> UUID:
    """Return a UUID only when it matches the stored dataset identifier format."""
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("Invalid dataset identifier.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValueError("Invalid dataset identifier.") from None
    if str(parsed) != value:
        raise ValueError("Invalid dataset identifier.")
    return parsed


DatasetId = Annotated[UUID, BeforeValidator(validate_canonical_dataset_id)]
