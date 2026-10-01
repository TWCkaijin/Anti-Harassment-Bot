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
_ALIASES = {alias: law for law, aliases in LAW_ALIASES.items() for alias in aliases}
_LAW_PATTERN = re.compile("|".join(sorted(_ALIASES, key=len, reverse=True)))
_ARTICLE_PATTERN = re.compile(
    r"第?\s*([0-9０-９零〇一二三四五六七八九十百千]+)"
    r"(?:\s*[-之]\s*([0-9０-９零〇一二三四五六七八九十百千]+))?\s*條"
    r"(?:\s*之\s*([0-9０-９零〇一二三四五六七八九十百千]+))?"
)


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


def lookup_key(law_name: str, number: str) -> str:
    return f"{law_name}:{article_number(number)}"


def legacy_conflict_reason(content: str, metadata: dict) -> str | None:
    """Block the known obsolete seed rule without declaring other rows verified."""
    if metadata.get("version") and metadata.get("source_url"):
        return None
    compact = re.sub(r"\s+", "", content)
    if "性騷擾防治法第13條" in compact and re.search(r"事件發生後[一1]年", compact):
        return "legacy_outdated_article_13_one_year_rule"
    return None
