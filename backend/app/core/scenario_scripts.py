"""Shared Firestore scenario skills selected without an extra model call."""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from typing import Any

from firebase_admin import firestore

from backend.app.core.logger import get_logger

logger = get_logger(__name__)

SCENARIO_SCRIPTS_COLLECTION = "scenario_scripts"
SCENARIO_SCRIPTS_CACHE_TTL_SECONDS = 60
_PHONE_PATTERN = re.compile(r"^[0-9+()-]{3,24}$")
_SCRIPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True)
class ScenarioAction:
    action: str
    phone_number: str
    label: str

    def public_dict(self) -> dict[str, str]:
        return {"action": self.action, "phone_number": self.phone_number, "label": self.label}


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


def _example_scenario_documents() -> dict[str, dict[str, Any]]:
    """Return the original built-in starter scenario."""
    return {
        "call_support": {
            "name": "電話求助",
            "enabled": True,
            "priority": 100,
            "trigger_keywords": ["撥打", "打電話", "電話", "113", "保護專線", "基金會", "專線"],
            "instruction": (
                "使用者正在詢問或表達要撥打電話求助。請先以尊重自主的語氣確認需求，"
                "並在確實提及求助電話或基金會時，於 action_buttons 僅選擇下列可用電話。"
                "不要編造號碼，也不要在使用者未表達聯絡意願時提供 action_buttons。"
            ),
            "actions": [
                {"action": "tel", "phone_number": "113", "label": "撥打 113 保護專線"},
                {"action": "tel", "phone_number": "02-2391-7133", "label": "撥打現代婦女基金會"},
                {"action": "tel", "phone_number": "02-8911-8595", "label": "撥打勵馨基金會"},
            ],
        },
    }


def _parse_action(value: Any) -> ScenarioAction | None:
    if not isinstance(value, dict):
        return None
    action, phone_number, label = value.get("action"), value.get("phone_number"), value.get("label")
    if action != "tel" or not isinstance(phone_number, str) or not isinstance(label, str):
        return None
    phone_number, label = phone_number.strip(), label.strip()
    if not _PHONE_PATTERN.fullmatch(phone_number) or not label or len(label) > 80:
        return None
    return ScenarioAction(action=action, phone_number=phone_number, label=label)


def _parse_script(script_id: str, data: dict[str, Any]) -> ScenarioScript | None:
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
    actions = tuple(action for value in data.get("actions", []) if (action := _parse_action(value)))
    priority = data.get("priority", 0)
    name = data.get("name")
    return ScenarioScript(
        script_id=script_id,
        name=name.strip()[:80] if isinstance(name, str) and name.strip() else script_id,
        enabled=bool(data.get("enabled", True)),
        priority=int(priority) if isinstance(priority, int | float) else 0,
        trigger_keywords=normalized_keywords,
        instruction=normalized_instruction[:12000],
        actions=actions,
    )


def _scripts_from_firestore() -> tuple[ScenarioScript, ...]:
    scripts: list[ScenarioScript] = []
    for snapshot in firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION).stream():
        script = _parse_script(snapshot.id, snapshot.to_dict() or {})
        if script:
            scripts.append(script)
    return tuple(sorted(scripts, key=lambda script: (-script.priority, script.name)))


def invalidate_scenario_scripts_cache() -> None:
    global _cached_at, _cached_scripts
    _cached_scripts, _cached_at = None, 0.0


def list_scenario_scripts(force_refresh: bool = False) -> tuple[ScenarioScript, ...]:
    """Return all shared scripts, including disabled ones, for Admin management."""
    global _cached_at, _cached_scripts
    now = monotonic()
    if (
        not force_refresh
        and _cached_scripts is not None
        and now - _cached_at < SCENARIO_SCRIPTS_CACHE_TTL_SECONDS
    ):
        return _cached_scripts
    try:
        _cached_scripts = _scripts_from_firestore()
    except Exception as exc:
        logger.warning("Failed to load scenario scripts: %s", exc)
        _cached_scripts = ()
    _cached_at = now
    return _cached_scripts


def get_scenario_scripts(force_refresh: bool = False) -> tuple[ScenarioScript, ...]:
    return tuple(script for script in list_scenario_scripts(force_refresh) if script.enabled)


def get_matching_scenario_scripts(user_message: str) -> tuple[ScenarioScript, ...]:
    normalized_message = user_message.lower()
    return tuple(
        script
        for script in get_scenario_scripts()
        if any(keyword in normalized_message for keyword in script.trigger_keywords)
    )


def validate_scenario_script(script_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not _SCRIPT_ID_PATTERN.fullmatch(script_id):
        raise ValueError("Skill ID must use lowercase letters, numbers, _ or -")
    script = _parse_script(script_id, payload)
    if not script:
        raise ValueError("Skill requires a name, instruction and at least one trigger keyword")
    if len(script.actions) != len(payload.get("actions", [])):
        raise ValueError("Skill actions must be valid tel actions with a label and phone number")
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
    firestore.client().collection(SCENARIO_SCRIPTS_COLLECTION).document(script_id).delete()
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
    for script in scripts:
        action_lines = "\n".join(
            f'- {action.label}: {{"action":"tel","phone_number":"{action.phone_number}"}}'
            for action in script.actions
        )
        blocks.append(
            f"## 情境腳本：{script.name}\n{script.instruction}\n可用 action_buttons：\n{action_lines or '無'}"
        )
    return "\n\n".join(blocks)


def available_actions(scripts: tuple[ScenarioScript, ...]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for script in scripts:
        for action in script.actions:
            key = (action.action, action.phone_number)
            if key not in seen:
                seen.add(key)
                actions.append(action.public_dict())
    return actions
