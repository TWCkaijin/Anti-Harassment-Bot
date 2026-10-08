"""Patch a single prediction column while preserving all other OOXML members.

The XML byte spans retain namespace declarations, extension metadata, drawings,
styles, and untouched cells exactly. No workbook-wide serialization is needed.
"""

from __future__ import annotations

import copy
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr

from tests.reliability.applicable_law_api_eval import (
    EvaluationInputError,
    _relationship_targets,
    _safe_sheet_member,
    column_index_from_reference,
    column_letter,
    read_xlsx,
)

PREDICTION_HEADER = "預測的適用法條"


@dataclass
class _Span:
    name: str
    attributes: dict[str, str]
    start: int
    open_end: int
    close_start: int = 0
    end: int = 0
    children: list[_Span] = field(default_factory=list)


def _spans(data: bytes) -> _Span:
    parser = expat.ParserCreate()
    stack: list[_Span] = []
    roots: list[_Span] = []

    def start(name: str, attributes: dict[str, str]) -> None:
        offset = parser.CurrentByteIndex
        # OOXML start tags contain quoted attributes; a literal '>' inside an
        # attribute is permitted, so scan until the first unquoted delimiter.
        quote = None
        finish = offset
        while finish < len(data):
            char = data[finish]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in (34, 39):
                quote = char
            elif char == 62:
                break
            finish += 1
        span = _Span(name, dict(attributes), offset, finish + 1)
        if stack:
            stack[-1].children.append(span)
        else:
            roots.append(span)
        stack.append(span)

    def end(_name: str) -> None:
        span = stack.pop()
        if data[span.start : span.open_end].rstrip().endswith(b"/>"):
            span.close_start = span.open_end
            span.end = span.open_end
        else:
            span.close_start = parser.CurrentByteIndex
            span.end = data.index(b">", span.close_start) + 1

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise EvaluationInputError("Invalid worksheet XML") from exc
    if len(roots) != 1:
        raise EvaluationInputError("Invalid worksheet root")
    return roots[0]


def _local(span: _Span) -> str:
    return span.name.rsplit(":", 1)[-1]


def worksheet_member(archive: zipfile.ZipFile, sheet_name: str) -> str:
    relationships = _relationship_targets(archive)
    root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    for sheet in root.findall(".//{*}sheet"):
        if sheet.attrib.get("name") != sheet_name:
            continue
        rel_id = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), None)
        if rel_id in relationships:
            return _safe_sheet_member(relationships[rel_id])
    raise EvaluationInputError(f"Missing target worksheet: {sheet_name}")


def _tag(name: str, attributes: dict[str, str]) -> bytes:
    attrs = "".join(f" {key}={quoteattr(value)}" for key, value in attributes.items())
    return f"<{name}{attrs}/>".encode()


def _patch_xml(data: bytes, column: str, predictions: dict[int, str]) -> bytes:
    root = _spans(data)
    target = column_index_from_reference(column) + 1
    prefix = root.name.rsplit(":", 1)[0] + ":" if ":" in root.name else ""
    sheet_data = next((c for c in root.children if _local(c) == "sheetData"), None)
    if sheet_data is None:
        raise EvaluationInputError("Worksheet has no sheetData")
    edits: list[tuple[int, int, bytes]] = []
    row_map = {int(row.attributes["r"]): row for row in sheet_data.children}
    for number, value in {1: PREDICTION_HEADER, **predictions}.items():
        row = row_map.get(number)
        if row is None or row.close_start == row.open_end:
            raise EvaluationInputError(f"Missing or empty worksheet row: {number}")
        cells = [cell for cell in row.children if _local(cell) == "c"]
        reference = f"{column}{number}"
        existing = next((cell for cell in cells if cell.attributes.get("r") == reference), None)
        nearby = next(
            (
                cell
                for cell in reversed(cells)
                if column_index_from_reference(cell.attributes["r"]) < target - 1
            ),
            None,
        )
        style = (existing or nearby).attributes.get("s") if existing or nearby else None
        attributes = {"r": reference, **({"s": style} if style is not None else {})}
        if value:
            attributes["t"] = "inlineStr"
            attrs = "".join(f" {key}={quoteattr(item)}" for key, item in attributes.items())
            cell_bytes = (
                f'<{prefix}c{attrs}><{prefix}is><{prefix}t xml:space="preserve">'
                f"{escape(value)}</{prefix}t></{prefix}is></{prefix}c>"
            ).encode()
        else:
            cell_bytes = _tag(f"{prefix}c", attributes)
        if existing:
            edits.append((existing.start, existing.end, cell_bytes))
        else:
            next_cell = next(
                (
                    cell
                    for cell in cells
                    if column_index_from_reference(cell.attributes["r"]) > target - 1
                ),
                None,
            )
            offset = next_cell.start if next_cell else row.close_start
            edits.append((offset, offset, cell_bytes))

    cols = next((c for c in root.children if _local(c) == "cols"), None)
    target_attributes = {"min": str(target), "max": str(target), "width": "40", "customWidth": "1"}
    if cols:
        content: list[bytes] = []
        inserted = False
        for item in cols.children:
            first, last = int(item.attributes["min"]), int(item.attributes["max"])
            if first <= target <= last:
                if first < target:
                    content.append(_tag(item.name, {**item.attributes, "max": str(target - 1)}))
                content.append(_tag(item.name, {**item.attributes, **target_attributes}))
                if target < last:
                    content.append(_tag(item.name, {**item.attributes, "min": str(target + 1)}))
                inserted = True
            else:
                if not inserted and first > target:
                    content.append(_tag(f"{prefix}col", target_attributes))
                    inserted = True
                content.append(data[item.start : item.end])
        if not inserted:
            content.append(_tag(f"{prefix}col", target_attributes))
        edits.append((cols.open_end, cols.close_start, b"".join(content)))
    else:
        content = f"<{prefix}cols>".encode() + _tag(f"{prefix}col", target_attributes)
        content += f"</{prefix}cols>".encode()
        edits.append((sheet_data.start, sheet_data.start, content))

    dimension = next((c for c in root.children if _local(c) == "dimension"), None)
    if dimension and "ref" in dimension.attributes:
        first, _, last = dimension.attributes["ref"].partition(":")
        last = last or first
        last_column = max(column_index_from_reference(last), target - 1)
        last_row = max(
            int("".join(char for char in last if char.isdigit())), max(predictions, default=1)
        )
        attributes = {
            **dimension.attributes,
            "ref": f"{first}:{column_letter(last_column)}{last_row}",
        }
        edits.append((dimension.start, dimension.end, _tag(dimension.name, attributes)))
    for start, end, replacement in sorted(edits, reverse=True):
        data = data[:start] + replacement + data[end:]
    ElementTree.fromstring(data)
    return data


def write_prediction_copy(
    source: Path, destination: Path, sheet: str, column: str, predictions: dict[int, str]
) -> None:
    """Validate an atomic XLSX copy with exactly one changed ZIP member."""
    if source.resolve() == destination.resolve():
        raise EvaluationInputError("Prediction output must differ from its source")
    source_sheets = read_xlsx(source)
    selected = next((s for s in source_sheets if s.name == sheet), None)
    if selected is None:
        raise EvaluationInputError(f"Missing target worksheet: {sheet}")
    target = column_index_from_reference(column)
    if any(cells.get(target, "") for _, cells in selected.rows):
        raise EvaluationInputError(f"Prediction column {column} already contains source values")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, suffix=".xlsx", delete=False
        ) as tmp:
            temporary = Path(tmp.name)
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary, "w") as output:
            member = worksheet_member(original, sheet)
            output.comment = original.comment
            for entry in original.infolist():
                content = original.read(entry.filename)
                if entry.filename == member:
                    content = _patch_xml(content, column, predictions)
                output.writestr(copy.copy(entry), content)
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary) as output:
            if output.testzip() is not None or original.namelist() != output.namelist():
                raise EvaluationInputError("Prediction workbook ZIP validation failed")
            for entry in original.infolist():
                if entry.filename != member and original.read(entry.filename) != output.read(
                    entry.filename
                ):
                    raise EvaluationInputError("An untouched OOXML member changed")
        copied_sheets = read_xlsx(temporary)
        for original_sheet, copied_sheet in zip(source_sheets, copied_sheets, strict=True):
            if original_sheet.name != copied_sheet.name:
                raise EvaluationInputError("Prediction worksheet order changed")
            expected_rows = dict(original_sheet.rows)
            actual_rows = dict(copied_sheet.rows)
            if original_sheet.name == sheet:
                expected_rows = {number: dict(cells) for number, cells in original_sheet.rows}
                for number, value in {1: PREDICTION_HEADER, **predictions}.items():
                    expected_rows[number][target] = value
            if expected_rows != actual_rows:
                raise EvaluationInputError("Prediction cell verification failed")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
