"""Incrementally decode only the top-level reply from a structured model response."""

import json

from backend.app.core.chat_response import ASSISTANT_REPLY_MAX_LENGTH

MAX_STREAM_RESPONSE_LENGTH = 256_000


class StreamReplyError(ValueError):
    """The streamed response cannot safely be decoded as the response contract."""


class ReplyJSONDecoder:
    """A bounded JSON scanner that never emits metadata or partial escape sequences.

    Complete JSON and response-schema validation still happen before the final event.
    This scanner validates structural boundaries before exposing the reply string.
    """

    def __init__(self):
        self._state = "start"
        self._raw: list[str] = []
        self._raw_length = 0
        self._keys: set[str] = set()
        self._key = ""
        self._string = ""
        self._escape = False
        self._unicode: str | None = None
        self._surrogate: int | None = None
        self._skip: list[str] = []
        self._nesting: list[str] = []
        self._skip_string = False
        self._skip_escape = False
        self._text_pending = ""
        self._reply_length = 0
        self.reply_seen = False

    def feed(self, chunk: str) -> str:
        self._raw_length += len(chunk)
        if self._raw_length > MAX_STREAM_RESPONSE_LENGTH:
            raise StreamReplyError("Streamed response exceeds the size limit")
        self._raw.append(chunk)
        output: list[str] = []
        for char in chunk:
            if self._state in {"key", "reply"}:
                self._read_string(char, output)
                continue
            if self._state == "skip":
                if self._read_skipped_value(char):
                    continue
                self._state = "after_value"
            if char in " \t\r\n":
                continue
            if self._state == "start" and char == "{":
                self._state = "key_or_end"
            elif self._state == "key_or_end" and char == "}":
                self._state = "done"
            elif self._state in {"key_or_end", "key_required"} and char == '"':
                self._state = "key"
                self._string = ""
            elif self._state == "colon" and char == ":":
                self._state = "value"
            elif self._state == "value":
                if self._key == "reply":
                    if char != '"':
                        raise StreamReplyError("Streamed reply must be a string")
                    self.reply_seen = True
                    self._state = "reply"
                else:
                    self._state = "skip"
                    self._skip = []
                    self._nesting = []
                    self._skip_string = False
                    self._skip_escape = False
                    if not self._read_skipped_value(char):
                        raise StreamReplyError("Missing JSON value")
            elif self._state == "after_value" and char == ",":
                self._state = "key_required"
            elif self._state == "after_value" and char == "}":
                self._state = "done"
            else:
                raise StreamReplyError("Invalid streamed JSON structure")
        return "".join(output)

    def finish(self) -> None:
        if self._state != "done" or not self.reply_seen:
            raise StreamReplyError("Incomplete streamed reply JSON")
        try:
            json.loads("".join(self._raw))
        except (ValueError, RecursionError) as exc:
            raise StreamReplyError("Invalid streamed response JSON") from exc

    def _read_skipped_value(self, char: str) -> bool:
        if not self._skip_string and not self._nesting and char in ",}":
            try:
                json.loads("".join(self._skip))
            except (ValueError, RecursionError) as exc:
                raise StreamReplyError("Invalid streamed metadata JSON") from exc
            return False
        self._skip.append(char)
        if self._skip_string:
            if self._skip_escape:
                self._skip_escape = False
            elif char == "\\":
                self._skip_escape = True
            elif char == '"':
                self._skip_string = False
        elif char == '"':
            self._skip_string = True
        elif char in "[{":
            self._nesting.append(char)
            if len(self._nesting) > 32:
                raise StreamReplyError("Streamed JSON nesting exceeds the limit")
        elif char in "]}" and (
            not self._nesting or self._nesting.pop() != {"]": "[", "}": "{"}[char]
        ):
            raise StreamReplyError("Unbalanced streamed JSON")
        return True

    def _read_string(self, char: str, output: list[str]) -> None:
        if self._unicode is not None:
            if char not in "0123456789abcdefABCDEF":
                raise StreamReplyError("Invalid JSON Unicode escape")
            self._unicode += char
            if len(self._unicode) == 4:
                self._append_character(chr(int(self._unicode, 16)), output)
                self._unicode = None
        elif self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
            else:
                escapes = {
                    '"': '"',
                    "\\": "\\",
                    "/": "/",
                    "b": "\b",
                    "f": "\f",
                    "n": "\n",
                    "r": "\r",
                    "t": "\t",
                }
                if char not in escapes:
                    raise StreamReplyError("Invalid JSON string escape")
                self._append_character(escapes[char], output)
        elif char == "\\":
            self._escape = True
        elif char == '"':
            if self._surrogate is not None:
                raise StreamReplyError("Unpaired JSON Unicode surrogate")
            if self._state == "key":
                if self._string in self._keys:
                    raise StreamReplyError("Duplicate top-level JSON key")
                self._keys.add(self._string)
                self._key = self._string
                self._state = "colon"
            else:
                output.append(self._text_pending)
                self._text_pending = ""
                self._state = "after_value"
        elif ord(char) < 32:
            raise StreamReplyError("Unescaped JSON control character")
        else:
            self._append_character(char, output)

    def _append_character(self, char: str, output: list[str]) -> None:
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDBFF:
            if self._surrogate is not None:
                raise StreamReplyError("Unpaired JSON Unicode surrogate")
            self._surrogate = codepoint
            return
        if 0xDC00 <= codepoint <= 0xDFFF:
            if self._surrogate is None:
                raise StreamReplyError("Unpaired JSON Unicode surrogate")
            char = chr(0x10000 + ((self._surrogate - 0xD800) << 10) + codepoint - 0xDC00)
            self._surrogate = None
        elif self._surrogate is not None:
            raise StreamReplyError("Unpaired JSON Unicode surrogate")
        if self._state == "key":
            self._string += char
            return
        self._reply_length += 1
        if self._reply_length > ASSISTANT_REPLY_MAX_LENGTH:
            raise StreamReplyError("Streamed reply exceeds the size limit")
        # Match AssistantChatResponse's handling of double-escaped newlines/tabs,
        # holding possible prefixes across chunks so users never see escape syntax.
        self._text_pending += char
        replacements = {"\\r\\n": "\n", "\\n": "\n", "\\t": "\t"}
        while self._text_pending:
            replacement = replacements.get(self._text_pending)
            if replacement is not None:
                output.append(replacement)
                self._text_pending = ""
            elif any(value.startswith(self._text_pending) for value in replacements):
                break
            else:
                output.append(self._text_pending[0])
                self._text_pending = self._text_pending[1:]
