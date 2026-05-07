from typing import Any

from app.schemas.material import UploadStatus


class UploadError(Exception):
    """Exception raised when an upload fails processing."""

    def __init__(
        self, status: UploadStatus, detail: str, result: dict[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.result = result


class MalwareError(UploadError):
    """Exception raised when an upload is flagged as malicious."""

    def __init__(self, detail: str) -> None:
        super().__init__(UploadStatus.MALICIOUS, detail)
