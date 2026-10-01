"""Decode complete v4 answer units without publishing partial JSON or metadata."""

import json

from backend.app.core.streaming_reply import MAX_STREAM_RESPONSE_LENGTH, StreamReplyError


def _distinct_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StreamReplyError("Duplicate streamed JSON key")
        result[key] = value
    return result


class AnswerUnitJSONDecoder:
    """Incremental root-object scanner with an atomic ``answer_units`` channel.

    Metadata is retained for routing/schema validation, never returned by feed.
    A unit is available as soon as its complete object arrives, before subsequent
    units or final metadata. ``finish`` is still required for a terminal success.
    """

    def __init__(self):
        self.raw = ""
        self.values = {}
        self.units = []
        self._position = 0
        self._state = "start"
        self._key = None
        self._keys = set()
        self._decoder = json.JSONDecoder(object_pairs_hook=_distinct_object)

    def _read(self):
        try:
            return self._decoder.raw_decode(self.raw, self._position)
        except json.JSONDecodeError:
            return None
        except (RecursionError, ValueError) as exc:
            raise StreamReplyError("Invalid streamed JSON value") from exc

    def feed(self, chunk: str) -> list[dict]:
        self.raw += chunk
        if len(self.raw) > MAX_STREAM_RESPONSE_LENGTH:
            raise StreamReplyError("Streamed response exceeds the size limit")
        ready = []
        while True:
            while self._position < len(self.raw) and self.raw[self._position].isspace():
                self._position += 1
            if self._position == len(self.raw):
                return ready
            char = self.raw[self._position]
            state = self._state
            if state == "start":
                if char != "{":
                    raise StreamReplyError("Expected a structured v4 response")
                self._state = "key_or_end"
            elif state in {"key_or_end", "key_required"}:
                if char == "}" and state == "key_or_end":
                    self._state = "done"
                else:
                    decoded = self._read()
                    if decoded is None:
                        return ready
                    key, end = decoded
                    if not isinstance(key, str) or key in self._keys:
                        raise StreamReplyError("Invalid or duplicate streamed JSON key")
                    self._keys.add(key)
                    self._key, self._position, self._state = key, end, "colon"
                    continue
            elif state == "colon":
                if char != ":":
                    raise StreamReplyError("Missing streamed JSON colon")
                self._state = "value"
            elif state == "value":
                if self._key == "answer_units":
                    if char != "[":
                        raise StreamReplyError("answer_units must be an array")
                    self._state = "unit_or_end"
                    self.values[self._key] = self.units
                else:
                    decoded = self._read()
                    if decoded is None:
                        return ready
                    value, end = decoded
                    # A number may continue in the next chunk. Wait for its
                    # delimiter rather than interpreting a partial primitive.
                    if end == len(self.raw) and not isinstance(value, (dict, list, str)):
                        return ready
                    self.values[self._key] = value
                    self._position, self._state = end, "after_value"
                    continue
            elif state in {"unit_or_end", "unit_required"}:
                if char == "]" and state == "unit_or_end":
                    self._state = "after_value"
                else:
                    decoded = self._read()
                    if decoded is None:
                        return ready
                    unit, end = decoded
                    if not isinstance(unit, dict) or len(self.units) >= 32:
                        raise StreamReplyError("Invalid or excessive answer units")
                    self.units.append(unit)
                    ready.append(unit)
                    self._position, self._state = end, "after_unit"
                    continue
            elif state == "after_unit":
                if char == ",":
                    self._state = "unit_required"
                elif char == "]":
                    self._state = "after_value"
                else:
                    raise StreamReplyError("Invalid answer unit delimiter")
            elif state == "after_value":
                if char == ",":
                    self._state = "key_required"
                elif char == "}":
                    self._state = "done"
                else:
                    raise StreamReplyError("Invalid streamed field delimiter")
            else:
                raise StreamReplyError("Unexpected data after streamed JSON")
            self._position += 1

    def finish(self) -> dict:
        if self._state != "done" or "answer_units" not in self._keys:
            raise StreamReplyError("Incomplete v4 response JSON")
        try:
            result = json.loads(self.raw, object_pairs_hook=_distinct_object)
        except (ValueError, RecursionError) as exc:
            raise StreamReplyError("Invalid v4 response JSON") from exc
        if not isinstance(result, dict) or result.get("answer_units") != self.units:
            raise StreamReplyError("Answer units changed after decoding")
        return result
