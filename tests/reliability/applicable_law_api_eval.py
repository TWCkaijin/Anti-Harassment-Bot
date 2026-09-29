#!/usr/bin/env python3
"""Evaluate applicable-law coverage through the public chat API.

This is deliberately an external black-box evaluator: it reads the source XLSX
files without importing the Flask app or agent implementation, sends one scenario
per API request, and checks the returned ``reply`` against the gold law set.

The project workbooks need two small profiles:

* ``chatgpt_scene.xlsx / Sheet1``: A=applicable law, B=scenario.
* ``gemini_scene.xlsx / 工作表1``: A=applicable law, B=scenario, E=additional
  law notes such as ``亦涉刑法``.  The other Gemini sheets duplicate this summary.

No label value is ever included in the question sent to the model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import statistics
import sys
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

import httpx

SCHEMA_VERSION = 1
DEFAULT_API_URL = "http://127.0.0.1:5000/api/v1/chat/"
DEFAULT_INPUTS = (
    Path("resource/chatgpt_scene.xlsx"),
    Path("resource/gemini_scene.xlsx"),
)
DEFAULT_OUTPUT_DIR = Path("tests/reliability/results")
APP_CHECK_HEADER = "X-Firebase-AppCheck"
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
MAX_CHAT_MESSAGE_LENGTH = 2_000


class EvaluationInputError(ValueError):
    """Raised when a workbook or CLI selection is not evaluable."""


class ChatApiError(RuntimeError):
    """Raised after a chat request exhausts its retry budget."""

    def __init__(self, message: str, events: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.events = events


@dataclass(frozen=True, slots=True)
class WorkbookProfile:
    sheets: tuple[str, ...]
    label_column: str
    scenario_columns: tuple[str, ...]
    secondary_columns: tuple[str, ...] = ()


WORKBOOK_PROFILES = {
    "chatgpt_scene.xlsx": WorkbookProfile(
        sheets=("Sheet1",),
        label_column="A",
        scenario_columns=("B",),
    ),
    "gemini_scene.xlsx": WorkbookProfile(
        sheets=("工作表1",),
        label_column="A",
        scenario_columns=("B",),
        secondary_columns=("E",),
    ),
}


# Canonical names are the values reported by the evaluator.  Alias matching is
# longest-first, so an official full name wins over a short form.
LAW_ALIASES: dict[str, tuple[str, ...]] = {
    "性別平等工作法": ("性別平等工作法", "性別工作平等法", "性工法"),
    "性別平等教育法": ("性別平等教育法", "性平法"),
    "性騷擾防治法": ("性騷擾防治法", "性騷法"),
    "跟蹤騷擾防制法": ("跟蹤騷擾防制法", "跟騷法", "跟騷"),
    "刑法": ("中華民國刑法", "刑法"),
}

# The unlabeled Gemini gold-note column contains a few truncated tokens.  They
# are accepted only while parsing gold labels; a model reply still needs a law
# name or established shorthand such as ``性騷法``.
LABEL_ONLY_ALIASES: dict[str, tuple[str, ...]] = {
    "性別平等工作法": ("性工",),
    "性騷擾防治法": ("性騷",),
}

_GENERIC_LAW_PATTERN = re.compile(r"[\u3400-\u9fff]{1,30}(?:條例|法)")
_LABEL_SPLIT_PATTERN = re.compile(r"[\n\r、，,；;／/()（）\[\]【】：:]+|亦涉|另涉|並涉|併涉")
_LEADING_LABEL_WORDS = re.compile(
    r"^(?:此行為|本行為|該行為|本案件|本案|案件調查|案件|行為|"
    r"可能涉及|可能適用|同時涉及|優先適用|主要適用|亦涉及|"
    r"適用|涉及|違反|依據|依|以|另|亦|並|涉|可|應)+"
)
_ZERO_WIDTH = re.compile("[\u200b-\u200d\ufeff]")
_NEGATED_BEFORE = (
    "並不適用",
    "並未適用",
    "不適用",
    "未適用",
    "並不涉及",
    "並未涉及",
    "不涉及",
    "未涉及",
    "不構成",
    "未構成",
    "不屬於",
    "非屬",
    "無涉",
)
_NEGATED_AFTER = (
    "並不適用",
    "不適用",
    "並不涉及",
    "不涉及",
    "不成立",
    "無涉",
)
_CONDITIONAL_MARKERS = ("若", "視", "可能", "如為", "如果", "條件")


@dataclass(frozen=True, slots=True)
class SheetData:
    name: str
    rows: tuple[tuple[int, dict[int, str]], ...]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    source_file: Path
    sheet: str
    excel_row: int
    scenario: str
    scenario_columns: tuple[str, ...]
    scenario_sha256: str
    primary_label_raw: str
    secondary_label_raw: tuple[str, ...]
    primary_laws: tuple[str, ...]
    secondary_laws: tuple[str, ...]
    required_laws: tuple[str, ...]
    compound: bool
    contains_yishe: bool
    conditional: bool
    gold_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReplyEvaluation:
    predicted_laws: tuple[str, ...]
    matched_laws: tuple[str, ...]
    missing_laws: tuple[str, ...]
    extra_laws: tuple[str, ...]
    negated_laws: tuple[str, ...]
    polarity_conflicts: tuple[str, ...]
    primary_pass: bool
    compound_pass: bool
    coverage_pass: bool
    strict_exact_pass: bool
    interaction_mode: str


def normalize_text(value: Any) -> str:
    """Normalize workbook/model text while preserving readable punctuation."""

    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = _ZERO_WIDTH.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()


def compact_text(value: Any) -> str:
    """Return a comparison form with Unicode and punctuation differences removed."""

    normalized = normalize_text(value).casefold()
    return "".join(char for char in normalized if char.isalnum() or "\u3400" <= char <= "\u9fff")


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _alias_entries() -> tuple[tuple[str, str, str], ...]:
    entries = [
        (compact_text(alias), canonical, alias)
        for canonical, aliases in LAW_ALIASES.items()
        for alias in aliases
    ]
    return tuple(sorted(entries, key=lambda item: len(item[0]), reverse=True))


_ALIASES_LONGEST_FIRST = _alias_entries()


def aliases_for(canonical: str) -> tuple[str, ...]:
    return LAW_ALIASES.get(canonical, (canonical,))


def canonicalize_law(candidate: str) -> str | None:
    """Map a label candidate to an official name, retaining unknown ``XX法`` names."""

    cleaned = compact_text(candidate)
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _LEADING_LABEL_WORDS.sub("", cleaned)

    for alias_compact, canonical, _ in _ALIASES_LONGEST_FIRST:
        if cleaned == alias_compact:
            return canonical
    if 2 <= len(cleaned) <= 32 and cleaned.endswith(("法", "條例")):
        return cleaned
    return None


def extract_laws(*texts: str) -> tuple[str, ...]:
    """Extract canonical law names from terse labels or a natural-language reply."""

    found: list[str] = []
    for raw_text in texts:
        normalized = normalize_text(raw_text)
        if not normalized:
            continue
        compact = compact_text(normalized)

        # Known aliases are scanned over the entire string.  This is what catches
        # malformed but present labels such as ``亦涉刑法性影像``.
        for alias_compact, canonical, _ in _ALIASES_LONGEST_FIRST:
            if alias_compact in compact:
                found.append(canonical)

        # Generic parsing keeps the evaluator usable for future ``亦涉XX法``
        # values without silently requiring an alias-map update.
        for fragment in _LABEL_SPLIT_PATTERN.split(normalized):
            for candidate in _GENERIC_LAW_PATTERN.findall(fragment):
                candidate_compact = compact_text(candidate)
                if any(
                    alias_compact in candidate_compact
                    for alias_compact, _, _ in _ALIASES_LONGEST_FIRST
                ):
                    # The known-alias scan above already captured the canonical
                    # law.  Do not emit prose prefixes such as ``舊稱...法`` as
                    # a second, invented law name.
                    continue
                canonical = canonicalize_law(candidate)
                if canonical:
                    found.append(canonical)

    return _ordered_unique(found)


def extract_label_laws(*texts: str) -> tuple[str, ...]:
    """Extract laws from gold labels, including their known truncated tokens."""

    found = list(extract_laws(*texts))
    for raw_text in texts:
        compact = compact_text(raw_text)
        for canonical, aliases in LABEL_ONLY_ALIASES.items():
            if any(compact_text(alias) in compact for alias in aliases):
                found.append(canonical)
    return _ordered_unique(found)


def _mention_polarities(reply: str, canonical: str) -> tuple[bool, bool]:
    compact = compact_text(reply)
    positive = False
    negative = False
    for alias in sorted(aliases_for(canonical), key=len, reverse=True):
        target = compact_text(alias)
        if not target:
            continue
        for match in re.finditer(re.escape(target), compact):
            before = compact[max(0, match.start() - 12) : match.start()]
            after = compact[match.end() : match.end() + 12]
            is_negative = any(before.endswith(marker) for marker in _NEGATED_BEFORE) or any(
                after.startswith(marker) for marker in _NEGATED_AFTER
            )
            negative = negative or is_negative
            positive = positive or not is_negative
    return positive, negative


def evaluate_reply(
    case: EvaluationCase,
    reply: str,
    *,
    interaction_mode: str = "answer",
) -> ReplyEvaluation:
    """Evaluate one reply using all-required-label (AND) semantics."""

    candidates = _ordered_unique((*case.required_laws, *extract_laws(reply)))
    predicted: list[str] = []
    negated: list[str] = []
    conflicts: list[str] = []
    for law in candidates:
        positive, negative = _mention_polarities(reply, law)
        if positive:
            predicted.append(law)
        if negative:
            negated.append(law)
        if positive and negative:
            conflicts.append(law)

    predicted_laws = _ordered_unique(predicted)
    matched = tuple(law for law in case.required_laws if law in predicted_laws)
    missing = tuple(law for law in case.required_laws if law not in predicted_laws)
    extra = tuple(law for law in predicted_laws if law not in case.required_laws)
    answered = interaction_mode != "clarify"
    coverage_pass = answered and not missing and not conflicts
    strict_exact_pass = coverage_pass and not extra
    primary_pass = answered and all(law in predicted_laws for law in case.primary_laws)
    compound_pass = (not case.compound) or coverage_pass

    return ReplyEvaluation(
        predicted_laws=predicted_laws,
        matched_laws=matched,
        missing_laws=missing,
        extra_laws=extra,
        negated_laws=_ordered_unique(negated),
        polarity_conflicts=_ordered_unique(conflicts),
        primary_pass=primary_pass,
        compound_pass=compound_pass,
        coverage_pass=coverage_pass,
        strict_exact_pass=strict_exact_pass,
        interaction_mode=interaction_mode,
    )


def column_index_from_reference(reference: str) -> int:
    match = re.match(r"([A-Za-z]+)", reference)
    if not match:
        raise EvaluationInputError(f"Invalid Excel cell/column reference: {reference!r}")
    value = 0
    for char in match.group(1).upper():
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def column_letter(index: int) -> str:
    if index < 0:
        raise ValueError("Excel column index cannot be negative")
    letters = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _shared_strings(archive: zipfile.ZipFile) -> tuple[str, ...]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return ()
    return tuple(
        "".join(node.text or "" for node in item.findall(".//{*}t"))
        for item in root.findall(".//{*}si")
    )


def _relationship_targets(archive: zipfile.ZipFile) -> dict[str, str]:
    root = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    return {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in root.findall(".//{*}Relationship")
        if "Id" in relationship.attrib and "Target" in relationship.attrib
    }


def _safe_sheet_member(target: str) -> str:
    if target.startswith("/"):
        member = target.lstrip("/")
    else:
        member = posixpath.normpath(posixpath.join("xl", target))
    path = PurePosixPath(member)
    if ".." in path.parts or not member.startswith("xl/"):
        raise EvaluationInputError(f"Unsafe worksheet target in XLSX: {target!r}")
    return member


def _cell_value(cell: ElementTree.Element, shared: Sequence[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t"))
    value_node = cell.find("{*}v")
    if value_node is None or value_node.text is None:
        return ""
    value = value_node.text
    if cell_type == "s":
        try:
            return shared[int(value)]
        except (IndexError, ValueError) as exc:
            raise EvaluationInputError(f"Invalid shared-string index: {value!r}") from exc
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def read_xlsx(path: Path) -> tuple[SheetData, ...]:
    """Read cell text from an OOXML workbook using only the standard library."""

    try:
        with zipfile.ZipFile(path) as archive:
            shared = _shared_strings(archive)
            relationships = _relationship_targets(archive)
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheets: list[SheetData] = []
            for sheet in workbook.findall(".//{*}sheet"):
                name = sheet.attrib.get("name", "")
                rel_id = next(
                    (value for key, value in sheet.attrib.items() if key.endswith("}id")),
                    None,
                )
                if not name or not rel_id or rel_id not in relationships:
                    continue
                member = _safe_sheet_member(relationships[rel_id])
                root = ElementTree.fromstring(archive.read(member))
                rows: list[tuple[int, dict[int, str]]] = []
                for inferred_row, row in enumerate(root.findall(".//{*}sheetData/{*}row"), start=1):
                    try:
                        row_number = int(row.attrib.get("r", inferred_row))
                    except ValueError as exc:
                        raise EvaluationInputError(
                            f"Invalid row number in {path.name}/{name}: {row.attrib.get('r')!r}"
                        ) from exc
                    cells: dict[int, str] = {}
                    inferred_column = 0
                    for cell in row.findall("{*}c"):
                        reference = cell.attrib.get("r")
                        index = (
                            column_index_from_reference(reference) if reference else inferred_column
                        )
                        inferred_column = index + 1
                        cells[index] = normalize_text(_cell_value(cell, shared))
                    rows.append((row_number, cells))
                sheets.append(SheetData(name=name, rows=tuple(rows)))
            return tuple(sheets)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as exc:
        raise EvaluationInputError(f"Cannot read XLSX {path}: {exc}") from exc


def _find_header_row(
    rows: Sequence[tuple[int, dict[int, str]]],
    label_header: str = "適用法規",
) -> tuple[int, dict[int, str]]:
    target = compact_text(label_header)
    for row_number, cells in rows:
        if any(compact_text(value) == target for value in cells.values()):
            return row_number, cells
    raise EvaluationInputError(f"Could not find header {label_header!r}")


def _resolve_column(token: str, headers: dict[int, str], *, purpose: str) -> int:
    if re.fullmatch(r"[A-Za-z]+", token.strip()):
        return column_index_from_reference(token.strip())
    target = compact_text(token)
    matches = [index for index, value in headers.items() if compact_text(value) == target]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise EvaluationInputError(f"Cannot find {purpose} column {token!r}")
    raise EvaluationInputError(f"Ambiguous {purpose} column {token!r}: {matches}")


def _profile_for(path: Path) -> WorkbookProfile:
    return WORKBOOK_PROFILES.get(
        path.name,
        WorkbookProfile(
            sheets=(),
            label_column="適用法規",
            scenario_columns=("情境",),
        ),
    )


def _case_prefix(path: Path) -> str:
    return {
        "chatgpt_scene.xlsx": "chatgpt",
        "gemini_scene.xlsx": "gemini",
    }.get(path.name, path.stem)


def _gold_warnings(secondary_raw: Sequence[str], secondary_laws: Sequence[str]) -> tuple[str, ...]:
    combined = "；".join(secondary_raw)
    warnings: list[str] = []
    if combined and not secondary_laws:
        warnings.append("supplemental_text_without_mapped_law")
    if "性影像" in compact_text(combined):
        warnings.append("ambiguous_topic:性影像")
    if any(marker in combined for marker in _CONDITIONAL_MARKERS):
        warnings.append("conditional_supplement")
    return _ordered_unique(warnings)


def cases_from_workbook(
    path: Path,
    *,
    sheets_override: Sequence[str] | None = None,
    label_column_override: str | None = None,
    scenario_columns_override: Sequence[str] | None = None,
    secondary_columns_override: Sequence[str] | None = None,
) -> tuple[EvaluationCase, ...]:
    if not path.is_file():
        raise EvaluationInputError(f"XLSX file does not exist: {path}")
    profile = _profile_for(path)
    workbook = {sheet.name: sheet for sheet in read_xlsx(path)}
    selected_sheets = tuple(sheets_override or profile.sheets or workbook.keys())
    missing_sheets = [name for name in selected_sheets if name not in workbook]
    if missing_sheets:
        raise EvaluationInputError(
            f"Workbook {path.name} does not contain sheet(s): {', '.join(missing_sheets)}"
        )

    label_token = label_column_override or profile.label_column
    scenario_tokens = tuple(scenario_columns_override or profile.scenario_columns)
    secondary_tokens = tuple(
        profile.secondary_columns
        if secondary_columns_override is None
        else secondary_columns_override
    )
    cases: list[EvaluationCase] = []

    for sheet_name in selected_sheets:
        sheet = workbook[sheet_name]
        header_row, headers = _find_header_row(sheet.rows)
        label_index = _resolve_column(label_token, headers, purpose="label")
        scenario_indices = tuple(
            _resolve_column(token, headers, purpose="scenario") for token in scenario_tokens
        )
        secondary_indices = tuple(
            _resolve_column(token, headers, purpose="secondary label") for token in secondary_tokens
        )

        for excel_row, cells in sheet.rows:
            if excel_row <= header_row:
                continue
            primary_raw = normalize_text(cells.get(label_index, ""))
            scenario_values = [normalize_text(cells.get(index, "")) for index in scenario_indices]
            if not primary_raw or not any(scenario_values):
                continue
            scenario_sections = [
                f"{headers.get(index) or column_letter(index)}：{value}"
                for index, value in zip(scenario_indices, scenario_values, strict=True)
                if value
            ]
            scenario = "\n".join(scenario_sections)
            secondary_raw = tuple(
                value
                for index in secondary_indices
                if (value := normalize_text(cells.get(index, "")))
            )
            primary_laws = extract_label_laws(primary_raw)
            secondary_laws = tuple(
                law for law in extract_label_laws(*secondary_raw) if law not in primary_laws
            )
            if not primary_laws:
                raise EvaluationInputError(
                    f"No law could be parsed from {path.name}/{sheet_name}/row {excel_row}: "
                    f"{primary_raw!r}"
                )
            required = _ordered_unique((*primary_laws, *secondary_laws))
            combined_secondary = "；".join(secondary_raw)
            cases.append(
                EvaluationCase(
                    case_id=f"{_case_prefix(path)}:{sheet_name}:{excel_row}",
                    source_file=path,
                    sheet=sheet_name,
                    excel_row=excel_row,
                    scenario=scenario,
                    scenario_columns=tuple(
                        headers.get(index) or column_letter(index) for index in scenario_indices
                    ),
                    scenario_sha256=hashlib.sha256(scenario.encode()).hexdigest(),
                    primary_label_raw=primary_raw,
                    secondary_label_raw=secondary_raw,
                    primary_laws=primary_laws,
                    secondary_laws=secondary_laws,
                    required_laws=required,
                    compound=len(required) > 1,
                    contains_yishe="亦涉" in combined_secondary,
                    conditional=any(
                        marker in combined_secondary for marker in _CONDITIONAL_MARKERS
                    ),
                    gold_warnings=_gold_warnings(secondary_raw, secondary_laws),
                )
            )
    return tuple(cases)


def build_question(case: EvaluationCase) -> str:
    """Build a stable, label-free law question for one scenario."""

    question = (
        "請依下列情境，判斷在臺灣可能適用的法律。請列出所有可能涉及的法規，"
        "若同時涉及多部法律，請全部列出，並使用明確的法規全名；若適用與當事人"
        "身分或場域有關，請一併說明條件。不要反問，直接回答法規與簡短理由。\n\n"
        f"情境資料：\n{case.scenario}\n\n問題：上述情境可能適用哪些法規？"
    )
    if len(question) > MAX_CHAT_MESSAGE_LENGTH:
        raise EvaluationInputError(
            f"Prompt for {case.case_id} is {len(question)} characters; API limit is "
            f"{MAX_CHAT_MESSAGE_LENGTH}"
        )
    question_compact = compact_text(question)
    label_values = (case.primary_label_raw, *case.secondary_label_raw)
    leaked = [value for value in label_values if value and compact_text(value) in question_compact]
    leaked.extend(
        alias
        for law in case.required_laws
        for alias in aliases_for(law)
        if compact_text(alias) in question_compact
    )
    if leaked:
        raise EvaluationInputError(
            f"Gold label leaked into prompt for {case.case_id}: {sorted(set(leaked))}"
        )
    return question


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("API response is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("API response JSON must be an object")
    return payload


def _retry_wait_seconds(
    response: httpx.Response | None,
    retry_index: int,
    backoff_seconds: float,
    max_retry_wait: float,
) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            if retry_after:
                return min(max(float(retry_after), 0.0), max_retry_wait)
        except ValueError:
            pass
    return min(backoff_seconds * (2**retry_index), max_retry_wait)


def call_chat_api(
    client: httpx.Client,
    *,
    api_url: str,
    question: str,
    use_rag: bool,
    app_check_token: str | None,
    retries: int,
    backoff_seconds: float,
    max_retry_wait: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], int, list[dict[str, Any]], float]:
    """Call chat with bounded transient retries and an auditable attempt trail."""

    headers = {"User-Agent": "rag-harass-bot-law-eval/1"}
    if app_check_token:
        headers[APP_CHECK_HEADER] = app_check_token
    request_payload = {"message": question, "history": [], "use_rag": use_rag}
    events: list[dict[str, Any]] = []
    total_started = time.perf_counter()

    for retry_index in range(retries + 1):
        started = time.perf_counter()
        response: httpx.Response | None = None
        try:
            response = client.post(api_url, json=request_payload, headers=headers)
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            try:
                payload = _response_json(response)
            except ValueError as exc:
                payload = {}
                parse_error = str(exc)
            else:
                parse_error = None

            retryable = response.status_code in RETRYABLE_STATUS_CODES or (
                isinstance(payload, dict) and payload.get("retryable") is True
            )
            event = {
                "attempt": retry_index + 1,
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "retryable": retryable,
            }
            if parse_error:
                event["error"] = parse_error
            elif not response.is_success:
                event["error"] = str(payload.get("detail") or f"HTTP {response.status_code}")[:500]
            events.append(event)

            if response.is_success and not parse_error:
                if not isinstance(payload.get("reply"), str):
                    event["error"] = "Successful response is missing string field 'reply'"
                    retryable = True
                else:
                    total_ms = round((time.perf_counter() - total_started) * 1000, 3)
                    return payload, response.status_code, events, total_ms

            if retry_index >= retries or not retryable:
                detail = event.get("error") or f"HTTP {response.status_code}"
                raise ChatApiError(str(detail), events)
        except httpx.RequestError as exc:
            events.append(
                {
                    "attempt": retry_index + 1,
                    "http_status": None,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "retryable": True,
                    "error": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            )
            if retry_index >= retries:
                raise ChatApiError(str(exc), events) from exc

        wait_seconds = _retry_wait_seconds(
            response,
            retry_index,
            backoff_seconds,
            max_retry_wait,
        )
        events[-1]["retry_wait_seconds"] = wait_seconds
        sleep(wait_seconds)

    raise AssertionError("unreachable")


def _case_manifest(case: EvaluationCase) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "source": {
            "file": str(case.source_file),
            "sheet": case.sheet,
            "excel_row": case.excel_row,
        },
        "scenario_sha256": case.scenario_sha256,
        "scenario_columns": list(case.scenario_columns),
        "primary_label_raw": case.primary_label_raw,
        "secondary_label_raw": list(case.secondary_label_raw),
        "primary_laws": list(case.primary_laws),
        "secondary_laws": list(case.secondary_laws),
        "required_laws": list(case.required_laws),
        "compound": case.compound,
        "contains_yishe": case.contains_yishe,
        "conditional": case.conditional,
        "gold_warnings": list(case.gold_warnings),
    }


def _api_error_record(
    case: EvaluationCase,
    *,
    run_index: int,
    question: str,
    api_url: str,
    use_rag: bool,
    error: ChatApiError,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        **_case_manifest(case),
        "run_index": run_index,
        "request": {"api_url": api_url, "use_rag": use_rag, "question": question},
        "api": {"ok": False, "events": error.events},
        "response": None,
        "evaluation": {
            "predicted_laws": [],
            "matched_laws": [],
            "missing_laws": list(case.required_laws),
            "extra_laws": [],
            "negated_laws": [],
            "polarity_conflicts": [],
            "primary_pass": False,
            "compound_pass": not case.compound,
            "coverage_pass": False,
            "strict_exact_pass": False,
            "interaction_mode": "api_error",
        },
        "error": {"type": "api_error", "message": str(error)[:1_000]},
    }


def _success_record(
    case: EvaluationCase,
    *,
    run_index: int,
    question: str,
    api_url: str,
    use_rag: bool,
    payload: dict[str, Any],
    http_status: int,
    events: list[dict[str, Any]],
    total_latency_ms: float,
    extra_law_policy: str,
) -> dict[str, Any]:
    reply = str(payload["reply"])
    interaction_mode = str(payload.get("interaction_mode") or "answer")
    evaluation = evaluate_reply(case, reply, interaction_mode=interaction_mode)
    evaluation_payload = asdict(evaluation)
    if extra_law_policy == "fail" and evaluation.extra_laws:
        evaluation_payload["coverage_pass"] = False
        evaluation_payload["compound_pass"] = not case.compound
    return {
        "schema_version": SCHEMA_VERSION,
        **_case_manifest(case),
        "run_index": run_index,
        "request": {"api_url": api_url, "use_rag": use_rag, "question": question},
        "api": {
            "ok": True,
            "http_status": http_status,
            "total_latency_ms": total_latency_ms,
            "events": events,
        },
        "response": {
            "reply": reply,
            "session_id": payload.get("session_id"),
            "rag_used": payload.get("rag_used"),
            "interaction_mode": interaction_mode,
            "clarifying_questions": payload.get("clarifying_questions", []),
        },
        "evaluation": evaluation_payload,
        "error": None,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def summarize(records: Sequence[dict[str, Any]], *, selected_case_count: int) -> dict[str, Any]:
    attempts = len(records)
    api_success = sum(record["api"]["ok"] for record in records)
    api_errors = attempts - api_success
    coverage_passed = sum(record["evaluation"]["coverage_pass"] for record in records)
    strict_passed = sum(record["evaluation"]["strict_exact_pass"] for record in records)
    primary_passed = sum(record["evaluation"]["primary_pass"] for record in records)
    compound_records = [record for record in records if record["compound"]]
    yishe_records = [record for record in records if record["contains_yishe"]]
    matched_laws = sum(len(record["evaluation"]["matched_laws"]) for record in records)
    required_laws = sum(len(record["required_laws"]) for record in records)
    predicted_laws = sum(len(record["evaluation"]["predicted_laws"]) for record in records)
    latencies = [
        float(record["api"]["total_latency_ms"]) for record in records if record["api"]["ok"]
    ]
    clarify_count = sum(record["evaluation"]["interaction_mode"] == "clarify" for record in records)

    return {
        "schema_version": SCHEMA_VERSION,
        "selected_case_count": selected_case_count,
        "attempt_count": attempts,
        "api_success_count": api_success,
        "api_error_count": api_errors,
        "api_success_rate": _ratio(api_success, attempts),
        "all_required_laws_passed": coverage_passed,
        "all_required_laws_pass_rate": _ratio(coverage_passed, attempts),
        "strict_exact_passed": strict_passed,
        "strict_exact_pass_rate": _ratio(strict_passed, attempts),
        "primary_law_pass_rate": _ratio(primary_passed, attempts),
        "law_micro_recall": _ratio(matched_laws, required_laws),
        "law_micro_precision": _ratio(matched_laws, predicted_laws),
        "compound_attempt_count": len(compound_records),
        "compound_all_covered_rate": _ratio(
            sum(record["evaluation"]["compound_pass"] for record in compound_records),
            len(compound_records),
        ),
        "contains_yishe_attempt_count": len(yishe_records),
        "contains_yishe_all_covered_rate": _ratio(
            sum(record["evaluation"]["coverage_pass"] for record in yishe_records),
            len(yishe_records),
        ),
        "clarification_response_count": clarify_count,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _select_cases(
    cases: Sequence[EvaluationCase], args: argparse.Namespace
) -> list[EvaluationCase]:
    selected = list(cases)
    if args.row:
        rows = set(args.row)
        selected = [case for case in selected if case.excel_row in rows]
    if args.case_id:
        case_ids = set(args.case_id)
        selected = [case for case in selected if case.case_id in case_ids]
    if args.where_law:
        requested: set[str] = set()
        for value in args.where_law:
            parsed = extract_label_laws(value)
            if not parsed:
                raise EvaluationInputError(f"Cannot parse --where-law value: {value!r}")
            requested.update(parsed)
        selected = [case for case in selected if requested.intersection(case.required_laws)]
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise EvaluationInputError("Case selection is empty")
    return selected


def _default_output_path() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_DIR / f"applicable-law-{timestamp}.jsonl"


def _prepare_output(path: Path, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise EvaluationInputError(f"Output already exists (use --overwrite): {path}")
    path.parent.mkdir(parents=True, exist_ok=True)


def _print_case(case: EvaluationCase, *, include_question: bool) -> None:
    payload = _case_manifest(case)
    payload["scenario_preview"] = case.scenario[:240]
    if include_question:
        payload["question"] = build_question(case)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read XLSX scenarios, call the existing chat API, and verify that every gold "
            "applicable law is present in reply."
        )
    )
    parser.add_argument(
        "--xlsx",
        action="append",
        type=Path,
        help="Input workbook; repeat for multiple files (default: both resource workbooks).",
    )
    parser.add_argument("--sheet", action="append", help="Sheet name override; repeat as needed.")
    parser.add_argument(
        "--label-column",
        help="Gold column header or Excel letter (profile default: A / 適用法規).",
    )
    parser.add_argument(
        "--scenario-column",
        action="append",
        help="Scenario header or Excel letter; repeat to include multiple fields.",
    )
    parser.add_argument(
        "--secondary-column",
        action="append",
        help=(
            "Additional gold-law column header/letter; repeat as needed. When omitted, "
            "gemini_scene.xlsx uses its unlabeled E column."
        ),
    )
    parser.add_argument("--row", action="append", type=int, help="Excel row number; repeatable.")
    parser.add_argument("--case-id", action="append", help="Exact case ID; repeatable.")
    parser.add_argument(
        "--where-law",
        action="append",
        help="Only cases requiring this canonical law or known shorthand; repeatable.",
    )
    parser.add_argument("--limit", type=int, help="Maximum selected cases after filtering.")
    parser.add_argument(
        "--all",
        action="store_true",
        dest="run_all",
        help="Explicitly allow all selected workbook rows to call the API.",
    )
    parser.add_argument("--list-cases", action="store_true", help="List cases without API calls.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show selected cases, parsed gold laws, and label-free prompts without API calls.",
    )
    parser.add_argument(
        "--strict-gold",
        action="store_true",
        help="Reject selected cases carrying ambiguous/conditional gold warnings.",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("RAG_EVAL_API_URL", DEFAULT_API_URL),
        help="Full chat endpoint URL (env: RAG_EVAL_API_URL).",
    )
    parser.add_argument(
        "--app-check-token-env",
        default="RAG_EVAL_APP_CHECK_TOKEN",
        help="Environment variable holding an optional Firebase App Check token.",
    )
    parser.add_argument("--no-rag", action="store_true", help="Send use_rag=false.")
    parser.add_argument("--runs", type=int, default=1, help="Independent API runs per case.")
    parser.add_argument("--timeout", type=float, default=190.0, help="Per-request timeout seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Transient retries per API run.")
    parser.add_argument("--retry-backoff", type=float, default=1.0)
    parser.add_argument("--max-retry-wait", type=float, default=30.0)
    parser.add_argument(
        "--delay",
        type=float,
        default=2.1,
        help="Delay between API runs; default stays below the current 30 requests/minute limit.",
    )
    parser.add_argument(
        "--extra-law-policy",
        choices=("warn", "fail", "ignore"),
        default="warn",
        help="Whether predicted laws absent from XLSX should fail coverage (default: warn).",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=1.0,
        help="Required all-laws-covered rate for exit 0.",
    )
    parser.add_argument(
        "--fail-under-compound",
        type=float,
        default=1.0,
        help="Required all-laws-covered rate for multi-law cases.",
    )
    parser.add_argument("--output", type=Path, help="JSONL result path (timestamped default).")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.runs < 1:
        raise EvaluationInputError("--runs must be at least 1")
    if args.retries < 0:
        raise EvaluationInputError("--retries cannot be negative")
    if args.limit is not None and args.limit < 1:
        raise EvaluationInputError("--limit must be at least 1")
    for name in ("timeout", "retry_backoff", "max_retry_wait", "delay"):
        if getattr(args, name) < 0:
            raise EvaluationInputError(f"--{name.replace('_', '-')} cannot be negative")
    for name in ("fail_under", "fail_under_compound"):
        value = getattr(args, name)
        if not 0 <= value <= 1:
            raise EvaluationInputError(f"--{name.replace('_', '-')} must be between 0 and 1")
    if not args.api_url.startswith(("http://", "https://")):
        raise EvaluationInputError("--api-url must be an http:// or https:// URL")
    has_bounded_selection = bool(args.row or args.case_id or args.where_law or args.limit)
    if not (args.list_cases or args.dry_run or args.run_all or has_bounded_selection):
        raise EvaluationInputError(
            "Refusing an unbounded paid API run: choose --row/--case-id/--where-law/--limit, "
            "or pass --all explicitly."
        )


def run(args: argparse.Namespace) -> int:
    _validate_args(args)
    paths = tuple(args.xlsx or DEFAULT_INPUTS)
    all_cases: list[EvaluationCase] = []
    for path in paths:
        all_cases.extend(
            cases_from_workbook(
                path,
                sheets_override=args.sheet,
                label_column_override=args.label_column,
                scenario_columns_override=args.scenario_column,
                secondary_columns_override=args.secondary_column,
            )
        )
    selected = _select_cases(all_cases, args)

    if args.strict_gold:
        warned = [case.case_id for case in selected if case.gold_warnings]
        if warned:
            raise EvaluationInputError(
                "Selected cases contain gold warnings under --strict-gold: "
                + ", ".join(warned[:20])
            )

    if args.list_cases or args.dry_run:
        for case in selected:
            _print_case(case, include_question=args.dry_run)
        print(f"selected_cases={len(selected)}", file=sys.stderr)
        return 0

    output_path = args.output or _default_output_path()
    _prepare_output(output_path, overwrite=args.overwrite)
    summary_path = output_path.with_suffix(".summary.json")
    _prepare_output(summary_path, overwrite=args.overwrite)
    app_check_token = os.getenv(args.app_check_token_env) or None
    use_rag = not args.no_rag
    records: list[dict[str, Any]] = []

    timeout = httpx.Timeout(args.timeout)
    with httpx.Client(timeout=timeout) as client, output_path.open("w", encoding="utf-8") as output:
        total_runs = len(selected) * args.runs
        completed_runs = 0
        for case in selected:
            question = build_question(case)
            for run_index in range(1, args.runs + 1):
                try:
                    payload, status, events, total_latency_ms = call_chat_api(
                        client,
                        api_url=args.api_url,
                        question=question,
                        use_rag=use_rag,
                        app_check_token=app_check_token,
                        retries=args.retries,
                        backoff_seconds=args.retry_backoff,
                        max_retry_wait=args.max_retry_wait,
                    )
                    record = _success_record(
                        case,
                        run_index=run_index,
                        question=question,
                        api_url=args.api_url,
                        use_rag=use_rag,
                        payload=payload,
                        http_status=status,
                        events=events,
                        total_latency_ms=total_latency_ms,
                        extra_law_policy=args.extra_law_policy,
                    )
                except ChatApiError as exc:
                    record = _api_error_record(
                        case,
                        run_index=run_index,
                        question=question,
                        api_url=args.api_url,
                        use_rag=use_rag,
                        error=exc,
                    )
                records.append(record)
                output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
                completed_runs += 1
                status_text = "PASS" if record["evaluation"]["coverage_pass"] else "FAIL"
                print(
                    f"[{completed_runs}/{total_runs}] {case.case_id} run={run_index} {status_text}",
                    file=sys.stderr,
                )
                if completed_runs < total_runs and args.delay:
                    time.sleep(args.delay)

    summary = summarize(records, selected_case_count=len(selected))
    summary.update(
        {
            "created_at": datetime.now(UTC).isoformat(),
            "api_url": args.api_url,
            "use_rag": use_rag,
            "runs_per_case": args.runs,
            "extra_law_policy": args.extra_law_policy,
            "app_check_header_present": app_check_token is not None,
            "input_workbooks": [
                {"path": str(path), "sha256": _sha256_file(path)} for path in paths
            ],
            "output_jsonl": str(output_path),
            "thresholds": {
                "all_required_laws_pass_rate": args.fail_under,
                "compound_all_covered_rate": args.fail_under_compound,
            },
        }
    )
    overall_rate = summary["all_required_laws_pass_rate"] or 0.0
    compound_rate = summary["compound_all_covered_rate"]
    thresholds_passed = overall_rate >= args.fail_under and (
        compound_rate is None or compound_rate >= args.fail_under_compound
    )
    summary["thresholds_passed"] = thresholds_passed
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"results={output_path}\nsummary={summary_path}", file=sys.stderr)

    if summary["api_error_count"]:
        return 3
    return 0 if thresholds_passed else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except EvaluationInputError as exc:
        print(f"input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
