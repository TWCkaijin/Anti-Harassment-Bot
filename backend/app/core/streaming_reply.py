"""Incrementally decode display text from a structured model response."""

import json

from backend.app.core.chat_response import ASSISTANT_REPLY_MAX_LENGTH

MAX_STREAM_RESPONSE_LENGTH = 256_000


class StreamReplyError(ValueError):
    """The streamed response cannot safely be decoded as the response contract."""


class ReplyJSONDecoder:
    """A bounded JSON scanner that exposes only reply and guidance display text.

    Complete JSON and response-schema validation still happen before the final event.
    Partial escape sequences, nested metadata and action selectors are never exposed.
    """

    def __init__(self):
        self._state = "start"
        self._raw: list[str] = []
        self._framing = "start"
        self._fence = ""
        self._fenced = False
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
        self._guidance: dict[str, str | list[str]] = {}
        self._last_guidance: dict[str, str | list[str]] = {}
        self._array_key = ""

    def feed(self, chunk: str) -> str:
        self._raw_length += len(chunk)
        if self._raw_length > MAX_STREAM_RESPONSE_LENGTH:
            raise StreamReplyError("Streamed response exceeds the size limit")
        output: list[str] = []
        for char in chunk:
            if not self._read_framing(char):
                continue
            self._raw.append(char)
            if self._state in {"key", "reply", "mode", "guidance_string"}:
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
                elif self._key == "interaction_mode":
                    if char != '"':
                        raise StreamReplyError("Streamed interaction mode must be a string")
                    self._state = "mode"
                    self._string = ""
                elif self._key in {"clarifying_questions", "suggested_replies"}:
                    if char != "[":
                        raise StreamReplyError("Streamed guidance must be an array")
                    self._array_key = self._key
                    self._guidance[self._array_key] = []
                    self._state = "guidance_value_or_end"
                else:
                    self._state = "skip"
                    self._skip = []
                    self._nesting = []
                    self._skip_string = False
                    self._skip_escape = False
                    if not self._read_skipped_value(char):
                        raise StreamReplyError("Missing JSON value")
            elif self._state == "guidance_value_or_end" and char == "]":
                self._state = "after_value"
            elif (
                self._state in {"guidance_value_or_end", "guidance_value_required"} and char == '"'
            ):
                values = self._guidance[self._array_key]
                limit = 3 if self._array_key == "clarifying_questions" else 4
                if len(values) >= limit:
                    raise StreamReplyError("Streamed guidance exceeds the item limit")
                values.append("")
                self._state = "guidance_string"
            elif self._state == "guidance_after_value" and char == ",":
                self._state = "guidance_value_required"
            elif self._state == "guidance_after_value" and char == "]":
                self._state = "after_value"
            elif self._state == "after_value" and char == ",":
                self._state = "key_required"
            elif self._state == "after_value" and char == "}":
                self._state = "done"
            else:
                raise StreamReplyError("Invalid streamed JSON structure")
        return "".join(output)

    def finish(self) -> None:
        if (
            self._state != "done"
            or not self.reply_seen
            or (self._fenced and self._framing != "closed")
        ):
            raise StreamReplyError("Incomplete streamed reply JSON")
        try:
            json.loads("".join(self._raw))
        except (ValueError, RecursionError) as exc:
            raise StreamReplyError("Invalid streamed response JSON") from exc

    def take_guidance(self) -> dict[str, str | list[str]] | None:
        """Return a changed cumulative preview; never include actions or hidden metadata."""
        snapshot = {
            key: [item.strip() for item in value if item.strip()]
            if isinstance(value, list)
            else value
            for key, value in self._guidance.items()
        }
        if snapshot == self._last_guidance:
            return None
        self._last_guidance = snapshot
        # Keep prior snapshots stable while later string fragments are appended.
        return {
            key: list(value) if isinstance(value, list) else value
            for key, value in snapshot.items()
        }

    def _read_framing(self, char: str) -> bool:
        """Recognize an optional JSON code fence without waiting for its closing fence."""
        if self._framing == "body":
            if self._state != "done":
                return True
            self._framing = "closing" if self._fenced else "closed"
        if self._framing == "closed":
            if not char.isspace():
                raise StreamReplyError("Unexpected data after streamed JSON")
            return False
        if self._framing == "closing":
            if not self._fence and char.isspace():
                return False
            self._fence += char
            if not "```".startswith(self._fence):
                raise StreamReplyError("Invalid closing JSON code fence")
            if self._fence == "```":
                self._framing = "closed"
            return False
        if self._framing == "opening":
            self._fence += char
            if not "```".startswith(self._fence):
                raise StreamReplyError("Invalid opening JSON code fence")
            if self._fence == "```":
                self._framing = "label"
                self._fence = ""
            return False
        if self._framing == "label":
            if char == "{" or char.isspace():
                if self._fence not in {"", "json"}:
                    raise StreamReplyError("Invalid JSON code fence language")
                self._fence = ""
                self._framing = "start_body"
            else:
                self._fence += char
                if not "json".startswith(self._fence):
                    raise StreamReplyError("Invalid JSON code fence language")
                return False
        if self._framing in {"start", "start_body"}:
            if char.isspace():
                return False
            if char == "{":
                self._framing = "body"
                return True
            if self._framing == "start" and char == "`":
                self._fenced = True
                self._framing = "opening"
                self._fence = "`"
                return False
            raise StreamReplyError("Invalid streamed JSON structure")
        return False

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
            elif self._state == "mode":
                if self._string not in {"answer", "clarify"}:
                    raise StreamReplyError("Invalid streamed interaction mode")
                self._guidance["interaction_mode"] = self._string
                self._state = "after_value"
            else:
                self._emit_text(self._text_pending, output)
                self._text_pending = ""
                self._state = (
                    "guidance_after_value" if self._state == "guidance_string" else "after_value"
                )
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
        if self._state in {"key", "mode"}:
            self._string += char
            return
        if self._state == "reply":
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
                self._emit_text(replacement, output)
                self._text_pending = ""
            elif any(value.startswith(self._text_pending) for value in replacements):
                break
            else:
                self._emit_text(self._text_pending[0], output)
                self._text_pending = self._text_pending[1:]

    def _emit_text(self, text: str, output: list[str]) -> None:
        if self._state == "reply":
            output.append(text)
            return
        values = self._guidance[self._array_key]
        values[-1] += text
        limit = 120 if self._array_key == "suggested_replies" else ASSISTANT_REPLY_MAX_LENGTH
        if len(values[-1].strip()) > limit:
            raise StreamReplyError("Streamed guidance exceeds the size limit")
