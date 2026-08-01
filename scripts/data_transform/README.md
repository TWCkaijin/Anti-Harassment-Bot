# Data Source Formats

The project keeps RAG source data under three standardized folders:

```text
data/documents/*.md
data/judgments/*.csv
data/remedy/*.md
```

## documents

General law or guidance documents are Markdown files under `data/documents/`.
Each file may include simple frontmatter:

```markdown
---
source: 性騷擾防治法第13條（申訴時效）
type: law
jurisdiction: 台灣
---
# 性騷擾防治法第13條（申訴時效）

文件內容...
```

Upload with:

```bash
python backend/scripts/ingest/documents_to_firestore.py --upload
```

## judgments

Judgment files are Markdown files under `data/judgments/`, one file per case.
The file name should use the judgment year plus case number, for example:

```text
115年-臺灣屏東地方法院115年度簡字第1033號刑事判決.md
```

Each file uses simple frontmatter:

```markdown
---
id: "6"
case_number: "臺灣屏東地方法院 115 年度簡字第 1033 號刑事判決"
judgment_date: "115 年 04 月 30 日"
cause: "違反性騷擾防治法"
jurisdiction: 屏東
---
# 臺灣屏東地方法院 115 年度簡字第 1033 號刑事判決

裁判日期：115 年 04 月 30 日
裁判案由：違反性騷擾防治法

裁判書內容...
```

Legacy CSV files can be split with:

```bash
python scripts/data_transform/standardize_judgments.py --input-dir data/raw/judgments --output-dir data/judgments
```

Upload with:

```bash
python backend/scripts/ingest/judgments_to_firestore.py --upload
```

## remedy

Remedy files are Markdown files under `data/remedy/`.
The current format is already standardized: one harassment/remedy type per file,
with a first-level heading as the source title.

```markdown
# 職場性騷擾

- 救濟管道...
```

Upload with:

```bash
python backend/scripts/ingest/remedies_to_firestore.py --upload
```
