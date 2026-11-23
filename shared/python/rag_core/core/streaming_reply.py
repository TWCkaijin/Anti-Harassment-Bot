"""Shared limits and errors for streamed model responses."""

MAX_STREAM_RESPONSE_LENGTH = 256_000


class StreamReplyError(ValueError):
    """The streamed response cannot safely be decoded as the response contract."""
