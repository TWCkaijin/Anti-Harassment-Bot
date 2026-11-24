"""Identifiers shared by offline legal imports and database-only retrieval."""

import re

LAW_ALIASES = {
    "性別平等工作法": ("性別平等工作法", "性別工作平等法", "性工法"),
    "性別平等教育法": ("性別平等教育法", "性平教育法"),
    "性騷擾防治法": ("性騷擾防治法", "性騷法"),
    "跟蹤騷擾防制法": ("跟蹤騷擾防制法", "跟騷法"),
    "中華民國刑法": ("中華民國刑法", "刑法"),
    "民法": ("民法",),
}
# Keep the six-law bulk import selection stable. A small separately reviewed
# procedural article can still participate in exact citation lookup.
QUERY_LAW_ALIASES = {**LAW_ALIASES, "刑事訴訟法": ("刑事訴訟法", "刑訴法")}
_ALIASES = {alias: law for law, aliases in QUERY_LAW_ALIASES.items() for alias in aliases}
_LAW_PATTERN = re.compile("|".join(sorted(_ALIASES, key=len, reverse=True)))
_ARTICLE_PATTERN = re.compile(
    r"第?\s*([0-9０-９零〇一二三四五六七八九十百千]+)"
    r"(?:\s*[-之]\s*([0-9０-９零〇一二三四五六七八九十百千]+))?\s*條"
    r"(?:\s*之\s*([0-9０-９零〇一二三四五六七八九十百千]+))?"
)
_CITATION_PATTERN = re.compile(rf"(?:{_LAW_PATTERN.pattern})\s*{_ARTICLE_PATTERN.pattern}")


def _number(value: str) -> int:
    value = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if value.isdecimal():
        return int(value)
    digits = dict(zip("零〇一二三四五六七八九", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9], strict=True))
    result = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            result += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return result + current


def article_number(value: str) -> str:
    """Canonical article number, e.g. 第三十二條之一 -> 32-1."""
    match = _ARTICLE_PATTERN.fullmatch(value.strip())
    if not match:
        if re.fullmatch(r"\d+(?:-\d+)?", value.strip()):
            return value.strip()
        raise ValueError("Invalid article number")
    base, sub, suffix = match.groups()
    return str(_number(base)) + (f"-{_number(sub or suffix)}" if sub or suffix else "")


def legal_references(query: str) -> list[tuple[str, str | None]]:
    """Read explicit law names and article citations; never infer case outcomes."""
    names = list(_LAW_PATTERN.finditer(query))
    references = []
    for index, match in enumerate(names):
        end = names[index + 1].start() if index + 1 < len(names) else len(query)
        # Article references belong only to the clause following that law name.
        clause = re.split(r"[。！？;；\n]", query[match.end() : end], maxsplit=1)[0][:100]
        articles = [article_number(item.group()) for item in _ARTICLE_PATTERN.finditer(clause)]
        references.extend((_ALIASES[match.group()], number) for number in articles or [None])
    return list(dict.fromkeys(references))


def citation_only_references(query: str) -> list[tuple[str, str]]:
    """Scope a bare named-article lookup without narrowing a narrative question."""
    if not _CITATION_PATTERN.search(query) or not re.fullmatch(
        r"[\s、，,;；和與及?？。!！]*", _CITATION_PATTERN.sub("", query)
    ):
        return []
    return [(law, number) for law, number in legal_references(query) if number is not None]


def is_law_document(metadata: dict) -> bool:
    return any(metadata.get(key) == "law" for key in ("data_type", "document_type", "type"))


def law_document_reference(metadata: dict) -> tuple[str, str] | None:
    name = metadata.get("law_name")
    if not isinstance(name, str):
        return None
    try:
        number = article_number(str(metadata.get("article_number", "")))
    except ValueError:
        return None
    return _ALIASES.get(name, name), number


def has_law_provenance(metadata: dict) -> bool:
    """Legacy law snippets without a source and version are not current-law evidence."""
    return all(
        isinstance(metadata.get(key), str) and bool(metadata[key].strip())
        for key in ("source_url", "version", "checked_at")
    )


def lookup_key(law_name: str, number: str) -> str:
    return f"{law_name}:{article_number(number)}"


def legacy_conflict_reason(content: str, metadata: dict) -> str | None:
    """Block known obsolete claims even if a legacy row carries plausible metadata."""
    if metadata.get("data_type") == "judgment" or str(metadata.get("collection", "")).endswith(
        "judgments"
    ):
        # A judgment's original wording is case evidence, not current legal guidance.
        return None
    compact = re.sub(r"\s+", "", content)
    source_text = re.sub(r"\s+", "", str(metadata.get("source", "")))
    old_article = (
        bool(re.search(r"性騷擾防治法第(?:13|十三)條", compact + source_text))
        or metadata.get("law_name") == "性騷擾防治法"
        and str(metadata.get("article_number", "")) in {"13", "十三"}
    )
    if old_article and re.search(r"(?:事件發生後|申訴(?:期限|時效)?(?:為|是)?)[一1]年", compact):
        return "legacy_outdated_article_13_one_year_rule"
    return None
