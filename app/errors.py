from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在"):
        super().__init__("NOT_FOUND", message, 404)
