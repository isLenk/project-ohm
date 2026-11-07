class ApiError(Exception):
    """Turned into `{"error": code, "message": message, ...extra}` with the given HTTP status."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found.")
