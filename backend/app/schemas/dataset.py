"""Dataset API response schemas."""

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class DatasetUploadResponse(BaseModel):
    """Acknowledgement for an accepted, non-persistent upload."""

    dataset_id: UUID
    filename: str
    size_bytes: int
    content_type: Optional[str]
    status: Literal["uploaded"]
