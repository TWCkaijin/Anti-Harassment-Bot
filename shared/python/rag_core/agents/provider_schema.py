"""Keep provider schema compilation within Gemini's complexity limits."""

from copy import deepcopy
from typing import Any

_LOCAL_CONSTRAINTS = {"minLength", "maxLength", "minItems", "pattern"}
_SCHEMA_MAPS = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}
_SCHEMA_CHILDREN = {
    "items",
    "additionalItems",
    "additionalProperties",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contains",
    "propertyNames",
    "not",
    "if",
    "then",
    "else",
}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}


def provider_response_format(model: str, response_format: dict[str, Any]) -> dict[str, Any]:
    """Copy Gemini schemas without costly bounds; local validation stays strict.

    Empty-array bounds still prohibit citations or draft answers at stages that
    cannot supply them. Route unions, literals and retrieval-specific enums are
    retained. Other models receive the original response format unchanged.
    """
    if not model.startswith("google/gemini-") or response_format.get("type") != "json_schema":
        return response_format

    result = deepcopy(response_format)

    def simplify(schema):
        if not isinstance(schema, dict):
            return
        for keyword in _LOCAL_CONSTRAINTS:
            schema.pop(keyword, None)
        if isinstance(schema.get("maxItems"), (int, float)) and schema["maxItems"] > 0:
            schema.pop("maxItems")
        for keyword in _SCHEMA_MAPS:
            for child in schema.get(keyword, {}).values():
                simplify(child)
        for keyword in _SCHEMA_CHILDREN:
            child = schema.get(keyword)
            if isinstance(child, list):
                for item in child:
                    simplify(item)
            else:
                simplify(child)
        for keyword in _SCHEMA_LISTS:
            for child in schema.get(keyword, []):
                simplify(child)

    simplify(result["json_schema"]["schema"])
    return result
