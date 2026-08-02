"""Custom application exception hierarchy."""

from http import HTTPStatus

__all__ = [
    "AppError",
    "NotFoundError",
    "BadRequestError",
    "UnauthorizedError",
    "ForbiddenError",
    "ConflictError",
    "RateLimitError",
    "ServiceUnavailableError",
]


class AppError(Exception):
    def __init__(self, status_code: int, detail: str, code: str | None = None):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = detail
        self.code = code


class NotFoundError(AppError):
    def __init__(self, detail: str = "Resource not found", code: str | None = None):
        super().__init__(status_code=HTTPStatus.NOT_FOUND, detail=detail, code=code)


class BadRequestError(AppError):
    def __init__(self, detail: str = "Bad request", code: str | None = None):
        super().__init__(status_code=HTTPStatus.BAD_REQUEST, detail=detail, code=code)


class UnauthorizedError(AppError):
    def __init__(self, detail: str = "Not authenticated", code: str | None = None):
        super().__init__(status_code=HTTPStatus.UNAUTHORIZED, detail=detail, code=code)


class ForbiddenError(AppError):
    def __init__(self, detail: str = "Not enough permissions", code: str | None = None):
        super().__init__(status_code=HTTPStatus.FORBIDDEN, detail=detail, code=code)


class ConflictError(AppError):
    def __init__(self, detail: str = "Conflict", code: str | None = None):
        super().__init__(status_code=HTTPStatus.CONFLICT, detail=detail, code=code)


class RateLimitError(AppError):
    def __init__(self, detail: str = "Too many requests", code: str | None = None):
        super().__init__(status_code=HTTPStatus.TOO_MANY_REQUESTS, detail=detail, code=code)


class ServiceUnavailableError(AppError):
    def __init__(self, detail: str = "Service unavailable", code: str | None = None):
        super().__init__(status_code=HTTPStatus.SERVICE_UNAVAILABLE, detail=detail, code=code)
