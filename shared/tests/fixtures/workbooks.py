"""Build deterministic OOXML fixtures from synthetic text only."""

import zipfile
from html import escape
from pathlib import Path


def write_scenario_workbook(path: Path, sheet: str, count: int = 3, *, label="GOLD_SECRET"):
    """Include rich namespaces, styles, notes, a split sheet, and a table member."""
    rows = [
        '<row r="1"><c r="A1" t="inlineStr"><is><t>適用法規</t></is></c><c r="B1" s="1" t="inlineStr"><is><t>情境</t></is></c></row>'
    ]
    for row in range(2, count + 2):
        rows.append(
            f'<row r="{row}" ht="30" customHeight="1"><c r="A{row}" s="2" t="inlineStr"><is><t>{escape(label)}</t></is></c>'
            f'<c r="B{row}" s="1" t="inlineStr"><is><t>情境{row} test@example.test</t></is></c>'
            f'<c r="E{row}" s="2" t="inlineStr"><is><t>NOTE_SECRET</t></is></c></row>'
        )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = b"synthetic preserved comment"
        archive.writestr(
            "xl/workbook.xml",
            f'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/><sheet name="工作" sheetId="2" r:id="rId2"/></sheets></workbook>''',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" mc:Ignorable="x14ac"><dimension ref="A1:E{count + 1}"/><sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="22" width="8" customWidth="1"/></cols><sheetData>{"".join(rows)}</sheetData><extLst><ext uri="preserved"><x14ac:synthetic value="retain"/></ext></extLst></worksheet>""",
        )
        archive.writestr(
            "xl/worksheets/sheet2.xml",
            """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1" t="inlineStr"><is><t>DUPLICATE_SECRET</t></is></c></row></sheetData></worksheet>""",
        )
        archive.writestr(
            "xl/styles.xml",
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs count="3"><xf/><xf/><xf/></cellXfs></styleSheet>',
        )
        archive.writestr("xl/tables/table1.xml", '<table name="Preserved" ref="B1:E4"/>')
        archive.writestr(
            "docProps/custom.xml", "<properties><synthetic>preserved</synthetic></properties>"
        )
