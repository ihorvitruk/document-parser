class ProcessingError(Exception):
    """Base class for errors raised while processing a document."""


class TransientError(ProcessingError):
    """A temporary failure (rate limit, network, 5xx). Worth retrying."""


class PermanentError(ProcessingError):
    """A failure that retrying cannot fix (corrupt file, invalid key). The message is
    shown to the user, so keep it readable."""


class UploadError(Exception):
    """A rejected upload; carries the HTTP status the API should answer with."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
