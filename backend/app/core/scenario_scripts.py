"""Packaged SKILL.md guidance with shared Firestore overrides and generic actions."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Any

from firebase_admin import firestore
from pydantic import ValidationError

from backend.app.core.chat_response import action_key, action_selector, configured_action_adapter
from backend.app.core.logger import get_logger

logger = get_logger(__name__)

SCENARIO_SCRIPTS_COLLECTION = "scenario_scripts"
SCENARIO_SCRIPTS_CACHE_TTL_SECONDS = 60
_SCRIPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_BUILTIN_SKILLS_DIRECTORY = Path(__file__).resolve().parents[1] / "skills"


@dataclass(frozen=True)
class ScenarioAction:
    action: str
    phone_number: str | None = None
    label: str = ""
    url: str | None = None
    id: str | None = None
    title: str | None = None
    options: tuple[dict[str, str], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        fields = {
            "action": self.action,
            "phone_number": self.phone_number,
            "url": self.url,
            "id": self.id,
        }
        public: dict[str, Any] = {**action_selector(fields), "label": self.label}
        if self.action == "options":
            public.update(title=self.title, options=[dict(option) for option in self.options])
        return public


@dataclass(frozen=True)
class ScenarioScript:
    script_id: str
    name: str
    enabled: bool
    priority: int
    trigger_keywords: tuple[str, ...]
    instruction: str
    actions: tuple[ScenarioAction, ...]

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.script_id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "trigger_keywords": list(self.trigger_keywords),
            "instruction": self.instruction,
            "actions": [action.public_dict() for action in self.actions],
        }


_cached_scripts: tuple[ScenarioScript, ...] | None = None
_cached_at = 0.0


@lru_cache(maxsize=1)
def _builtin_scenario_documents() -> dict[str, dict[str, Any]]:
    """Load JSON-compatible YAML frontmatter and the actual Markdown instructions."""
    documents: dict[str, dict[str, Any]] = {}
    for path in sorted(_BUILTIN_SKILLS_DIRECTORY.glob("*/SKILL.md")):
        try:
            source = path.read_text(encoding="utf-8")
            match = re.fullmatch(r"---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)", source, re.DOTALL)
            if not match:
                raise ValueError("SKILL.md requires frontmatter delimiters")
            metadata = json.loads(match.group(1))
            scenario = dict(metadata["metadata"]["scenario"])
            script_id = scenario.pop("id")
            scenario["instruction"] = match.group(2).strip()
            documents[script_id] = validate_scenario_script(script_id, scenario)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.error("Invalid packaged Skill %s: %s", path.name, exc)
    return documents


def _example_scenario_documents() -> dict[str, dict[str, Any]]:
    """Return independent copies so admin seeding never mutates packaged defaults."""
    return json.loads(json.dumps(_builtin_scenario_documents(), ensure_ascii=False))


def _parse_action(value: Any) -> ScenarioAction | None:
    try:
        validated = configured_action_adapter.validate_python(value).model_dump()
    except (ValidationError, TypeError):
        return None
    if "options" in validated:
        validated["options"] = tuple(validated["options"])
    return ScenarioAction(**validated)


def _parse_script(script_id: str, data: dict[str, Any]) -> ScenarioScript | None:
    if not isinstance(data, dict) or not _SCRIPT_ID_PATTERN.fullmatch(script_id):
        return None
    instruction, keywords = data.get("instruction"), data.get("trigger_keywords")
    if not isinstance(instruction, str) or not isinstance(keywords, list):
        return None
    normalized_instruction = instruction.strip()
    normalized_keywords = tuple(
        keyword.strip().lower()
        for keyword in keywords
        if isinstance(keyword, str) and keyword.strip()
    )
    if not normalized_instruction or not normalized_keywords:
        return None
    raw_actions = data.get("actions", [])
    actions = (
        tuple(action for value in raw_actions if (action := _parse_action(value)))
        if isinstance(raw_actions, list)
        else ()
    )
    priority = data.get("priority", 0)
    name = data.get("name")
    return ScenarioScript(
        script_id=script_id,
        name=name.strip()[:80] if isinstance(name, str) and name.strip() else script_id,
        enabled=data.get("enabled", True) is True,
        priority=(
            priority
            if isinstance(priority, int)
            else int(priority)
            if isinstance(priority, float) and math.isfinite(priority)
            else 0
        ),
        trigger_keywords=normalized_keywords,
        instruction=normalized_instruction[:12000],
        actions=actions,
    )


def _scripts_from_firestore() -> tuple[ScenarioScript, ...]:
    scripts: list[ScenarioScript] = []
    builtins = _builtin_scenario_documents()
    for snapshot in firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION).stream():
        data = snapshot.to_dict()
        if not isinstance(data, dict):
            logger.warning("Ignoring malformed Skill document %s", snapshot.id)
            continue
        # A sparse disabled override must still suppress its packaged counterpart.
        payload = {**builtins.get(snapshot.id, {}), **data}
        script = _parse_script(snapshot.id, payload)
        if script:
            scripts.append(script)
            if not isinstance(payload.get("actions", []), list) or len(script.actions) != len(
                payload.get("actions", [])
            ):
                logger.warning("Ignored invalid action configuration in Skill %s", snapshot.id)
        elif snapshot.id in builtins and data.get("enabled") is False:
            disabled = _parse_script(snapshot.id, {**builtins[snapshot.id], "enabled": False})
            if disabled:
                scripts.append(disabled)
        else:
            logger.warning("Ignoring invalid Skill document %s", snapshot.id)
    return tuple(sorted(scripts, key=lambda script: (-script.priority, script.name)))


def invalidate_scenario_scripts_cache() -> None:
    global _cached_at, _cached_scripts
    _cached_scripts, _cached_at = None, 0.0


def list_scenario_scripts(force_refresh: bool = False) -> tuple[ScenarioScript, ...]:
    """Return packaged Skills merged by ID with Firestore, including disabled overrides."""
    global _cached_at, _cached_scripts
    now = monotonic()
    if (
        not force_refresh
        and _cached_scripts is not None
        and now - _cached_at < SCENARIO_SCRIPTS_CACHE_TTL_SECONDS
    ):
        return _cached_scripts
    merged = {
        script_id: script
        for script_id, payload in _builtin_scenario_documents().items()
        if (script := _parse_script(script_id, payload))
    }
    try:
        overrides = _scripts_from_firestore()
        merged.update({script.script_id: script for script in overrides})
    except Exception as exc:
        logger.warning("Failed to load scenario scripts: %s", exc)
        # Preserve the last loaded overrides, especially disabled Skills, during outages.
        if _cached_scripts is not None:
            merged.update({script.script_id: script for script in _cached_scripts})
    _cached_scripts = tuple(
        sorted(merged.values(), key=lambda script: (-script.priority, script.name))
    )
    _cached_at = now
    return _cached_scripts


def get_scenario_scripts(force_refresh: bool = False) -> tuple[ScenarioScript, ...]:
    return tuple(script for script in list_scenario_scripts(force_refresh) if script.enabled)


def get_matching_scenario_scripts(
    user_message: str, history: list[dict[str, Any]] | None = None
) -> tuple[ScenarioScript, ...]:
    recent_messages = [
        message.get("content", "")
        for message in (history or [])[-2:]
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    ]
    normalized_message = "\n".join([*recent_messages, user_message]).lower()
    return tuple(
        script
        for script in get_scenario_scripts()
        if any(keyword in normalized_message for keyword in script.trigger_keywords)
    )


def validate_scenario_script(script_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not _SCRIPT_ID_PATTERN.fullmatch(script_id):
        raise ValueError("Skill ID must use lowercase letters, numbers, _ or -")
    if not isinstance(payload, dict):
        raise ValueError("Skill must be an object")
    if not isinstance(payload.get("actions", []), list):
        raise ValueError("Skill actions must be an array")
    if not isinstance(payload.get("enabled", True), bool):
        raise ValueError("Skill enabled must be a boolean")
    script = _parse_script(script_id, payload)
    if not script:
        raise ValueError("Skill requires a name, instruction and at least one trigger keyword")
    if len(script.actions) != len(payload.get("actions", [])):
        raise ValueError(
            "Skill actions must be valid tel, url or options actions with their required fields"
        )
    keys = [action_key(action.public_dict()) for action in script.actions]
    if len(keys) != len(set(keys)):
        raise ValueError("Skill actions must use distinct selectors")
    return {
        "name": script.name,
        "enabled": script.enabled,
        "priority": script.priority,
        "trigger_keywords": list(script.trigger_keywords),
        "instruction": script.instruction,
        "actions": [action.public_dict() for action in script.actions],
    }


def upsert_scenario_script(
    script_id: str, payload: dict[str, Any], updated_by: str
) -> ScenarioScript:
    document = validate_scenario_script(script_id, payload)
    document["updated_by"] = updated_by
    document["updated_at"] = firestore.SERVER_TIMESTAMP
    firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION).document(script_id).set(document)
    invalidate_scenario_scripts_cache()
    return next(script for script in list_scenario_scripts(True) if script.script_id == script_id)


def delete_scenario_script(script_id: str) -> None:
    reference = firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION).document(script_id)
    builtins = _builtin_scenario_documents()
    if script_id in builtins:
        reference.set(
            {**builtins[script_id], "enabled": False, "updated_at": firestore.SERVER_TIMESTAMP}
        )
    else:
        reference.delete()
    invalidate_scenario_scripts_cache()


def seed_example_scenario_scripts(updated_by: str = "admin") -> tuple[ScenarioScript, ...]:
    collection = firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION)
    example_documents = _example_scenario_documents()
    for script_id, payload in example_documents.items():
        reference = collection.document(script_id)
        if reference.get().exists:
            continue
        document = validate_scenario_script(script_id, payload)
        document["updated_by"] = updated_by
        document["updated_at"] = firestore.SERVER_TIMESTAMP
        reference.set(document)

    invalidate_scenario_scripts_cache()
    example_ids = set(example_documents)
    return tuple(
        script for script in list_scenario_scripts(True) if script.script_id in example_ids
    )


def format_scenario_instruction(scripts: tuple[ScenarioScript, ...]) -> str:
    blocks: list[str] = []
    seen: set[tuple[str, str]] = set()
    for script in scripts:
        unique_actions: list[ScenarioAction] = []
        duplicate_selectors: list[dict[str, str]] = []
        for action in script.actions:
            key = action_key(action.public_dict())
            if key in seen:
                duplicate_selectors.append(action_selector(action.public_dict()))
            else:
                seen.add(key)
                unique_actions.append(action)
        action_lines = "\n".join(
            f"- {action.label}: {json.dumps(action_selector(action.public_dict()), ensure_ascii=False)}"
            + (
                f"；選項問答內容：{json.dumps({'title': action.title, 'options': action.options}, ensure_ascii=False)}"
                if action.action == "options"
                else ""
            )
            for action in unique_actions
        )
        duplicate_note = (
            "\n以下重複 selector 使用前面較高優先序 Skill 已列出的標籤與選項，"
            "不使用本段指示中與其衝突的動作定義："
            + json.dumps(duplicate_selectors, ensure_ascii=False)
            if duplicate_selectors
            else ""
        )
        blocks.append(
            f"## 情境腳本：{script.name}\n{script.instruction}"
            f"\n可用 action_buttons：\n{action_lines or '無新增動作'}{duplicate_note}"
        )
    return "\n\n".join(blocks)


def available_actions(scripts: tuple[ScenarioScript, ...]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for script in scripts:
        for action in script.actions:
            key = action_key(action.public_dict())
            if key not in seen:
                seen.add(key)
                actions.append(action.public_dict())
    return actions
