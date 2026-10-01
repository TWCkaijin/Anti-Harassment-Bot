"""Request-scoped case facts and trusted questions. Nothing here persists a case."""

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_serializer,
    model_validator,
)

SUMMARY_MAX_LENGTH = 4000
MAX_CONTEXT_REVISION = 9007199254740991

FactKey = Literal[
    "subject_role",
    "other_role",
    "relationship",
    "work_related",
    "internship_related",
    "education_related",
    "behavior",
    "ongoing",
    "event_time",
    "age_group",
    "city",
    "desired_help",
]
FactStatus = Literal["provided", "unknown", "declined"]
FACT_KEYS = tuple(FactKey.__args__)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


FactText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


def fact_text(value: str | list[str] | None) -> str:
    return "、".join(value) if isinstance(value, list) else (value or "")


class CaseFact(StrictModel):
    status: FactStatus
    value: FactText | list[FactText] | None = None

    @model_validator(mode="after")
    def valid_value(self):
        if self.status == "provided" and not self.value:
            raise ValueError("Provided facts require a value")
        if self.status != "provided" and self.value is not None:
            raise ValueError("Unknown or declined facts must not carry a value")
        if isinstance(self.value, list) and (
            not 1 <= len(self.value) <= 4 or len(set(self.value)) != len(self.value)
        ):
            raise ValueError("Multi-value facts require one to four distinct values")
        return self


class CaseContext(StrictModel):
    schema_version: Literal[1, 2, 3] = 1
    revision: int = Field(default=0, ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    facts: dict[FactKey, CaseFact] = Field(default_factory=dict, max_length=len(FACT_KEYS))
    summary: str = Field(default="", max_length=SUMMARY_MAX_LENGTH)
    summary_origin: Literal["model", "user", "migration"] = "model"

    @model_validator(mode="before")
    @classmethod
    def reject_summary_in_legacy_context(cls, value):
        if (
            isinstance(value, dict)
            and value.get("schema_version", 1) != 3
            and {"summary", "summary_origin"}.intersection(value)
        ):
            raise ValueError("Text summaries require case schema 3")
        return value

    @model_validator(mode="after")
    def legacy_values_are_strings(self):
        if self.schema_version == 3 and self.facts:
            raise ValueError("Case schema 3 uses a text summary and cannot contain legacy facts")
        if self.schema_version == 1 and any(
            isinstance(fact.value, list) for fact in self.facts.values()
        ):
            raise ValueError("Multi-value facts require case schema 2")
        return self

    @model_serializer(mode="wrap")
    def serialize_context(self, handler):
        result = handler(self)
        if self.schema_version != 3:
            # Keep v1/v2 serialized snapshots valid when read by the same model.
            result.pop("summary", None)
            result.pop("summary_origin", None)
        return result


class SummaryUpdate(StrictModel):
    """A proposed replacement for exactly one request's context revision."""

    base_revision: int = Field(ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    summary: str = Field(max_length=SUMMARY_MAX_LENGTH)
    evidence: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=12)


class ClarificationAnswer(CaseFact):
    question_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    fact_key: FactKey | None = None
    context_revision: int | None = Field(default=None, ge=0, le=MAX_CONTEXT_REVISION, strict=True)
    validation_token: str | None = Field(default=None, max_length=8192)
    context_scope: Literal["personal", "scenario"] | None = None
    selection_mode: Literal["single", "multiple"] | None = None
    max_selections: int | None = Field(default=None, ge=1, le=4)
    allowed_values: list[FactText] | None = Field(default=None, max_length=3)


class FactUpdate(CaseFact):
    fact_key: FactKey
    evidence: str = Field(default="", max_length=500)
    kind: Literal["explicit", "confirmation"] = "confirmation"


QUESTION_CONFIG = {
    "other_role": (
        "對方和你的關係是什麼？不需要提供姓名。",
        "雙方關係會影響適合的處理方向。",
        ["主管", "同事", "顧客", "老師", "學生", "陌生人", "其他角色"],
    ),
    "subject_role": (
        "事件發生時，你的身分是什麼？",
        "確認身分有助於區分工作與教育相關的處理方向。",
        ["受僱者", "求職者", "學生", "實習生", "其他角色"],
    ),
    "work_related": (
        "這件事是否與你執行工作有關？",
        "工作關聯會影響處理管道，不需要提供公司名稱。",
        ["是", "否"],
    ),
    "internship_related": (
        "事件是否發生在實習期間或與實習有關？",
        "實習關係可能需要進一步區分學校與實習單位的角色。",
        ["是", "否"],
    ),
    "education_related": (
        "這件事是否涉及學校成員或教育活動？",
        "需要了解教育關係，不需要學校名稱。",
        ["是", "否"],
    ),
    "behavior": (
        "你願意說明涉及哪一類行為嗎？",
        "行為類型有助於找到相關資訊，不需要描述私密細節。",
        ["言語或訊息", "肢體碰觸", "影像", "跟蹤", "其他行為"],
    ),
    "ongoing": ("這類行為目前是否仍在持續？", "是否持續會影響目前需要的協助。", ["是", "否"]),
    "event_time": (
        "事件大約發生在什麼時候？",
        "只有討論期限或法規版本時才需要時間資訊，可提供大致年月。",
        [],
    ),
    "age_group": (
        "事件發生時，你是否已成年？",
        "年齡條件可能影響期限及保護程序，不需要生日。",
        ["已成年", "未成年"],
    ),
    "city": ("你希望查詢哪個縣市的求助管道？", "縣市有助於尋找受理資源，不需要地址。", []),
    "desired_help": (
        "目前你最希望獲得哪一方面的協助？",
        "可以依你的需要安排下一步。",
        ["先整理情況", "了解法律資訊", "了解申訴管道", "尋找支持資源"],
    ),
    "relationship": (
        "雙方是否有指導、管理或其他關係？",
        "關係有助於理解情境，不需要姓名或機構名稱。",
        ["管理關係", "教學或指導關係", "沒有上述關係"],
    ),
}


def clarification_for(key: FactKey, revision: int) -> dict:
    question, reason, options = QUESTION_CONFIG[key]
    return {
        "question_id": f"case.{key}.{revision}",
        "fact_key": key,
        "question": question,
        "reason": reason,
        "options": [{"label": value, "value": value} for value in options],
    }


def is_urgent(message: str) -> bool:
    return any(
        term in message
        for term in (
            "正在被",
            "現在被",
            "不安全",
            "立即危險",
            "自殺",
            "輕生",
            "限制行動",
            "正在跟蹤",
            "威脅",
        )
    )


def is_case_question(message: str, context: CaseContext) -> bool:
    return bool(context.facts) or bool(
        re.search(
            r"(?:我|本人|朋友).{0,15}(?:被|遭|遇到|主管|同事|公司|學校|實習)|(?:受僱者|求職者|主管|部屬|下屬|學生|老師).{0,120}(?:騷擾|環抱|觸碰|言語|遭|強迫)",
            message,
        )
    )


def next_required_fact(message: str, context: CaseContext) -> FactKey | None:
    """Only choose a question for a concrete missing fact, never a complete intake form."""
    if is_urgent(message) or not is_case_question(message, context):
        return None
    if not any(
        word in message for word in ("適用", "申訴", "法律", "怎麼辦", "期限", "時效", "提告")
    ):
        return None
    for key in ("other_role", "subject_role"):
        if key not in context.facts:
            return key
    if (
        any(term in message for term in ("期限", "時效", "來得及"))
        and "event_time" not in context.facts
    ):
        return "event_time"
    return None


def evidence_supports(update: FactUpdate) -> bool:
    """Conservative literal checks; ambiguous extractions remain proposals for confirmation."""
    if update.status != "provided" or not update.value:
        return False
    if isinstance(update.value, list):
        return all(
            evidence_supports(update.model_copy(update={"value": item})) for item in update.value
        )
    value, evidence = update.value, update.evidence
    if update.fact_key == "subject_role":
        return bool(
            re.search(
                r"(?:我|本人)(?:目前|當時)?(?:是|身為)(?:一位|個)?" + re.escape(value), evidence
            )
        )
    if update.fact_key == "other_role":
        return bool(
            re.search(r"(?:對方是|行為人是|我的|我們的)(?:一位|個)?" + re.escape(value), evidence)
        )
    if update.fact_key in {"work_related", "internship_related", "education_related", "ongoing"}:
        yes = {
            "work_related": ("我在執行職務", "我正在工作", "我在上班"),
            "internship_related": ("我在實習", "我的實習", "實習期間"),
            "education_related": ("在教育活動中", "參加教育活動", "教育活動期間"),
            "ongoing": ("一直持續", "還在持續", "持續發生"),
        }
        return (
            value == "是"
            and any(term in evidence for term in yes[update.fact_key])
            and not any(term in evidence for term in ("不", "沒有", "並非", "不是"))
        )
    if update.fact_key == "age_group":
        # Never match the substring 成年 inside 未成年 or infer an age from role.
        patterns = {
            "成年": r"(?:我|本人)(?:已|已經)(?:成年)",
            "已成年": r"(?:我|本人)(?:已|已經)(?:成年)",
            "未成年": r"(?:我|本人)(?:尚)?未成年",
        }
        return bool(patterns.get(value) and re.search(patterns[value], evidence))
    # Dates, categories and locations must be literally present, not inferred.
    cues = {
        "behavior": ("碰", "摸", "言語", "訊息", "影像", "跟蹤", "騷擾"),
        "event_time": ("發生", "事件", "去年", "今年", "月", "日", "年"),
        "city": ("市", "縣"),
        "desired_help": ("想", "希望", "需要"),
        "relationship": ("關係", "指導", "管理"),
    }
    return value in evidence and any(cue in evidence for cue in cues.get(update.fact_key, ()))


def validate_fact_updates(
    updates: list[FactUpdate],
    context: CaseContext,
    user_texts: list[str],
    *,
    diagnostics: list | None = None,
) -> list[dict]:
    result = []
    seen = set()

    def record(update, decision, reason):
        if diagnostics is not None:
            diagnostics.append(
                {"fact_key": update.fact_key, "decision": decision, "reason": reason}
            )

    for update in updates:
        if update.fact_key in seen:
            record(update, "rejected", "duplicate_fact_in_output")
            continue
        seen.add(update.fact_key)
        if not update.evidence or not any(update.evidence in text for text in user_texts):
            record(update, "rejected", "no_verbatim_user_evidence")
            continue
        existing = context.facts.get(update.fact_key)
        if existing and existing.status == update.status and existing.value == update.value:
            record(update, "unchanged", "already_recorded")
            continue
        # Verify ownership around the matching clause, not punctuation elsewhere
        # in the request. A subsequent question or quoted perpetrator words must
        # not erase an earlier first-person assertion.
        anchor = update.evidence
        if isinstance(update.value, str) and update.fact_key in {"subject_role", "other_role"}:
            pattern = (
                r"(?:我|本人)(?:目前|當時)?(?:是|身為)(?:一位|個)?"
                if update.fact_key == "subject_role"
                else r"(?:對方是|行為人是|我的|我們的)(?:一位|個)?"
            ) + re.escape(update.value)
            match = re.search(pattern, update.evidence)
            if match:
                anchor = match.group(0)
        asserted = any(_asserted_occurrence(text, anchor) for text in user_texts)
        explicit = (
            update.kind == "explicit"
            and evidence_supports(update)
            and existing is None
            and asserted
        )
        record(
            update,
            "accepted" if explicit else "confirmation",
            "explicit_supported_assertion"
            if explicit
            else "existing_fact_conflict"
            if existing
            else "uncertain_ownership_or_value",
        )
        result.append(
            {
                **update.model_dump(exclude_none=True),
                "kind": "explicit" if explicit else "confirmation",
            }
        )
    return result


def _asserted_occurrence(text: str, evidence: str) -> bool:
    for match in re.finditer(re.escape(evidence), text):
        before = text[: match.start()]
        sentence_start = max(before.rfind(mark) for mark in ("。", "！", "!", "\n")) + 1
        prefix = text[sentence_start : match.start()]
        tail = re.split(r"[，,。！!；;\n]", text[match.end() :], maxsplit=1)[0]
        clause_prefix = re.split(r"[，,；;]", prefix)[-1]
        assertion_clause = clause_prefix + re.split(r"[，,；;。！!\n]", evidence, maxsplit=1)[0]
        if any(word in prefix for word in ("假如", "假設", "如果", "若", "例如", "例題")):
            continue
        if any(
            word in assertion_clause
            for word in (
                "不是",
                "並非",
                "不再",
                "沒有",
                "不確定",
                "可能",
                "好像",
                "聽說",
                "有人說",
                "他說",
                "她說",
                "轉述",
            )
        ):
            continue
        if (
            prefix.count("「") > prefix.count("」")
            or prefix.count("『") > prefix.count("』")
            or prefix.count('"') % 2
        ):
            continue
        if any(mark in tail for mark in ("？", "?")) or tail.startswith(("嗎", "是否")):
            continue
        return True
    return False
