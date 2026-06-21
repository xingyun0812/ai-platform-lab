"""AI Platform Lab Python SDK — exceptions."""
from __future__ import annotations


class AIPlatformError(Exception):
    """Base exception for all AI Platform Lab SDK errors."""


class APIError(AIPlatformError):
    """Raised for non-2xx HTTP responses."""

    def __init__(self, status_code: int, message: str, body: object = None) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover
        return f"APIError(status_code={self.status_code!r}, message={self.message!r})"


class AuthenticationError(APIError):
    """Raised for 401 / 403 responses."""


class NotFoundError(APIError):
    """Raised for 404 responses."""


class RateLimitError(APIError):
    """Raised for 429 responses."""
