import csv

from backend.scripts.common.firestore_ingest import (
    build_document_documents,
    build_judgment_documents,
    build_remedy_documents,
)


def test_build_document_documents_from_markdown(tmp_path):
    path = tmp_path / "law.md"
    path.write_text(
        "\n".join(
            [
                "---",
                "source: 測試法規",
                "type: law",
                "jurisdiction: 台灣",
                "---",
                "# 測試法規",
                "",
                "性騷擾申訴期限測試內容。",
            ]
        ),
        encoding="utf-8",
    )

    docs = build_document_documents(path)

    assert len(docs) == 1
    assert docs[0].collection_name == "rag_documents"
    assert docs[0].metadata["data_type"] == "document"
    assert docs[0].metadata["source"] == "測試法規"
    assert "性騷擾申訴期限" in docs[0].content


def test_build_judgment_documents_from_csv(tmp_path):
    path = tmp_path / "judgments.csv"
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["id", "裁判字號", "裁判日期", "裁判案由", "裁判書內容"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "id": "1",
                "裁判字號": "臺灣屏東地方法院 113 年度測字第 1 號",
                "裁判日期": "113 年 01 月 01 日",
                "裁判案由": "違反性騷擾防治法",
                "裁判書內容": "這是一段判決內容。",
            }
        )

    docs = build_judgment_documents(path)

    assert len(docs) == 1
    assert docs[0].collection_name == "rag_judgments"
    assert docs[0].metadata["data_type"] == "judgment"
    assert docs[0].metadata["case_number"] == "臺灣屏東地方法院 113 年度測字第 1 號"


def test_build_judgment_documents_from_markdown(tmp_path):
    path = tmp_path / "113年-臺灣屏東地方法院113年度測字第1號.md"
    path.write_text(
        "\n".join(
            [
                "---",
                'id: "1"',
                'case_number: "臺灣屏東地方法院 113 年度測字第 1 號"',
                'judgment_date: "113 年 01 月 01 日"',
                'cause: "違反性騷擾防治法"',
                "jurisdiction: 屏東",
                "---",
                "# 臺灣屏東地方法院 113 年度測字第 1 號",
                "",
                "裁判日期：113 年 01 月 01 日",
                "裁判案由：違反性騷擾防治法",
                "",
                "這是一段判決內容。",
            ]
        ),
        encoding="utf-8",
    )

    docs = build_judgment_documents(path)

    assert len(docs) == 1
    assert docs[0].collection_name == "rag_judgments"
    assert docs[0].metadata["data_type"] == "judgment"
    assert docs[0].metadata["source_row_id"] == "1"
    assert docs[0].metadata["case_number"] == "臺灣屏東地方法院 113 年度測字第 1 號"


def test_build_remedy_documents_from_markdown(tmp_path):
    path = tmp_path / "職場性騷擾.md"
    path.write_text(
        "# 職場性騷擾\n\n- 可向勞青處申訴。\n- 可撥打 113。",
        encoding="utf-8",
    )

    docs = build_remedy_documents(path)

    assert len(docs) == 1
    assert docs[0].collection_name == "rag_remedies"
    assert docs[0].metadata["data_type"] == "remedy"
    assert docs[0].metadata["section_title"] == "職場性騷擾"
    assert "labor_department" in docs[0].metadata["remedy_channels"]
