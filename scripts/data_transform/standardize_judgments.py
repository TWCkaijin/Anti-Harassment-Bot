"""Split legacy judgment CSV files into one Markdown file per case."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

REQUIRED_FIELDS = ["id", "裁判字號", "裁判日期", "裁判案由", "裁判書內容"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Split judgment CSV rows into Markdown files.")
    parser.add_argument("--input-dir", type=Path, default=Path("data/judgments"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/judgments"))
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _filename_part(value: str) -> str:
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[\\/:*?\"<>|]+", "-", value)
    return value.strip("-")


def _year_from(row: dict[str, str]) -> str:
    date = row.get("裁判日期", "")
    case_number = row.get("裁判字號", "")
    match = re.search(r"(\d{2,4})\s*年", date) or re.search(r"(\d{2,4})\s*年度", case_number)
    return f"{match.group(1)}年" if match else "未知年份"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _case_markdown(row: dict[str, str]) -> str:
    source_id = row["id"]
    case_number = row["裁判字號"]
    judgment_date = row["裁判日期"]
    cause = row["裁判案由"]
    body = row["裁判書內容"]
    return "\n".join(
        [
            "---",
            f"id: {_quote(source_id)}",
            f"case_number: {_quote(case_number)}",
            f"judgment_date: {_quote(judgment_date)}",
            f"cause: {_quote(cause)}",
            "jurisdiction: 屏東",
            "---",
            f"# {case_number}",
            "",
            f"裁判日期：{judgment_date}",
            f"裁判案由：{cause}",
            "",
            body,
            "",
        ]
    )


def split_csv(input_path: Path, output_dir: Path, overwrite: bool) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    count = 0
    with input_path.open(encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        missing = set(REQUIRED_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{input_path} missing required fields: {sorted(missing)}")

        for row in reader:
            normalized = {field: (row.get(field) or "").strip() for field in REQUIRED_FIELDS}
            if not normalized["裁判書內容"]:
                continue
            base_name = f"{_year_from(normalized)}-{_filename_part(normalized['裁判字號'])}"
            file_name = f"{base_name}.md"
            if file_name in used_names:
                file_name = f"{base_name}-{_filename_part(normalized['id'])}.md"
            used_names.add(file_name)
            output_path = output_dir / file_name
            if output_path.exists() and not overwrite:
                print(f"skip existing {output_path}")
                continue
            output_path.write_text(_case_markdown(normalized), encoding="utf-8")
            count += 1
    print(f"wrote {count} judgment files from {input_path}")
    return count


def main() -> None:
    args = parse_args()
    candidates = [
        path
        for path in sorted(args.input_dir.glob("*.csv"))
        if "判決" in path.name or "judgment" in path.name.lower()
    ]
    if not candidates:
        print("No judgment CSV files found.")
        return
    for path in candidates:
        split_csv(path, args.output_dir, args.overwrite)


if __name__ == "__main__":
    main()
