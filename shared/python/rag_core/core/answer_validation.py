"""Shared answer validation: claim checks, legal-text patterns and source binding.

Used by the v4 chat runner to repair or block unsupported legal claims, URLs and
contacts before any answer unit is displayed.
"""

import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

from pydantic import Field

from rag_core.core.case_context import (
    StrictModel,
)
from rag_core.core.chat_response import validate_action_url
from rag_core.core.referral_policy import is_private_referral_sentence
from rag_core.rag.legal_lookup import legal_references


class QuestionOption(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)


def strict_schema(node):
    """Adapt Pydantic's schema to the provider's strict structured-output subset."""
    if isinstance(node, dict):
        node = {key: strict_schema(value) for key, value in node.items() if key != "default"}
        if "discriminator" in node and "oneOf" in node:
            # Pydantic's tagged unions emit oneOf/discriminator, which OpenAI
            # rejects. Their distinct literal tags keep these branches disjoint
            # with anyOf too; the local Pydantic contract stays unchanged.
            node["anyOf"] = node.pop("oneOf")
            node.pop("discriminator")
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
    elif isinstance(node, list):
        node = [strict_schema(value) for value in node]
    return node


def build_source(doc, data_type: str) -> dict:
    metadata = doc.metadata
    collection = metadata.get("collection") or data_type
    identifier = f"{collection}/{doc.doc_id}"
    url = metadata.get("source_url") or metadata.get("url")
    try:
        url = validate_action_url(url) if isinstance(url, str) else None
    except ValueError:
        url = None
    return {
        "label": str(metadata.get("source") or doc.doc_id),
        "type": data_type,
        "collection": collection,
        "doc_id": identifier,
        "source_url": url,
        "article": metadata.get("article") if isinstance(metadata.get("article"), str) else None,
        "version": metadata.get("version") if isinstance(metadata.get("version"), str) else None,
        "distance": metadata.get("distance")
        if isinstance(metadata.get("distance"), (int, float))
        else None,
        **{
            key: metadata[key]
            for key in (
                "law_name",
                "article_number",
                "checked_at",
                "effective_date",
                "promulgation_date",
                "promulgated_date",
                "promulgated_date_scope",
                "effective_date_scope",
                "effective_note",
            )
            if isinstance(metadata.get(key), str)
        },
    }


def _sentences(text: str) -> list[str]:
    # Callers join these pieces without separators. Keep every original byte,
    # including blank lines, list indentation, and repeated punctuation.
    return re.findall(r"[^。！？!?\r\n]+[。！？!?]*|[。！？!?]+|[\r\n]+", text)


def _article_number(value: str) -> str:
    value = re.sub(r"\s+", "", value.replace("第", "").replace("條", "")).replace("之", "-")
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "兩": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }

    def part(text):
        if text.isdigit():
            return str(int(text))
        total = current = 0
        for char in text:
            if char in digits:
                current = digits[char]
            elif char in {"十", "百", "千"}:
                total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                current = 0
            else:
                return text
        return str(total + current)

    return "-".join(part(item) for item in value.split("-"))


_NUMBER = r"(?:[0-9０-９]+(?:\.[0-9０-９]+)?|[零〇一二三四五六七八九十百千半兩]+)"


_ARTICLE_REF = re.compile(rf"第\s*{_NUMBER}(?:\s*(?:之|-)\s*{_NUMBER})?\s*條(?:\s*之\s*{_NUMBER})?")


_DURATION = re.compile(
    rf"({_NUMBER}(?:\s*(?:至|到|[~～–])\s*{_NUMBER})?)\s*(?:個)?\s*(年|月|日|天|週|小時)"
)


_NO_DEADLINE_CUE = re.compile(
    r"(?:沒有|並?未|無)(?:設置|訂定|規定)?(?:明確|固定)?(?:的)?(?:法定)?"
    r"(?:行政)?(?:申訴|告訴|追訴)?(?:期限|時效)"
    r"|(?:申訴|告訴|追訴)(?:沒有|並?未|無)(?:明確|法定|固定)?(?:的)?(?:期限|時效)"
    r"|不受.{0,8}(?:期限|時效)限制"
)


_CALENDAR_DATE = re.compile(
    rf"(?:民國\s*)?{_NUMBER}\s*年\s*{_NUMBER}\s*月(?:\s*{_NUMBER}\s*[日號])?"
    rf"|{_NUMBER}\s*月\s*{_NUMBER}\s*[日號]"
    rf"|(?:民國\s*)?{_NUMBER}\s*年"
    r"|(?<![0-9])(?:[12][0-9]{3}|[0-9]{3})[-/][0-9]{1,2}[-/][0-9]{1,2}"
)


_LAW_NAMES = (
    "性別平等工作法",
    "性別工作平等法",
    "性工法",
    "性別平等教育法",
    "性平教育法",
    "性騷擾防治法",
    "性騷法",
    "跟蹤騷擾防制法",
    "跟騷法",
    "中華民國刑法",
    "刑法",
    "民法",
)


_LAW_REF = re.compile(r"《[^》]+法》|" + "|".join(_LAW_NAMES))


_LAW_CANONICAL = {
    "性別工作平等法": "性別平等工作法",
    "性工法": "性別平等工作法",
    "性平教育法": "性別平等教育法",
    "性騷法": "性騷擾防治法",
    "跟騷法": "跟蹤騷擾防制法",
    "刑法": "中華民國刑法",
}


_CURRENT_VERSION = re.compile(r"(?:現行|目前有效|最新)(?=[^，,；;。！？!?\n]{0,8}(?:法|條文))")


_URL_REF = re.compile(r"https://[^\s<>\]\)）}，。；;]+")


_WEB_ADDRESS = r"(?:https?://|www\.)[^\s<>\[\]\)）}，。；、！？]+"


_ANSWER_LINK = re.compile(
    rf"\[(?P<label>[^\]\r\n]+)\]\(<?(?P<markdown>{_WEB_ADDRESS})>?\)|"
    rf"<(?P<autolink>{_WEB_ADDRESS})>|(?P<bare>{_WEB_ADDRESS})",
    re.IGNORECASE,
)


_URL_TEXT_SUFFIX = re.compile(r"^[\u3400-\u9fff。，；、！？]|^[.!?]+$")


_PHONE_REF = re.compile(
    r"(?<![0-9])(?:1(?:10|13|19)|\(?0[2-9]\)?[-\s]?\d{3,4}[-\s]?\d{3,4})(?![0-9])"
)


_CORRECTION_CUE = re.compile(r"(?:更正|說錯|錯誤|不對|不是|應該是|我記得|你確定|你說的|你提供的)")


_MASKED_OFFICIAL_PHONE_DECLARATION = re.compile(
    r"^\s*(?P<agency>[\u4e00-\u9fff]{2,10}(?:市政府|縣政府|市府|縣府|區公所|鄉公所|鎮公所))"
    r"(?:的)?(?:總機|聯絡)?電話(?:號碼)?\s*(?:是|為)\s*\[電話號碼\]\s*[。.!！]?\s*$"
)


_UNVERIFIED_OFFICIAL_PHONE_MARKER = "unverified_masked_official_phone"


_PRIOR_JUDGMENT_SOURCE_REQUEST = re.compile(
    r"(?:你|您)(?:剛才|先前|之前|前面|上面|上次)?(?:的)?"
    r"(?:判斷|結論|回答|說法)(?:所)?(?:依據|引用)"
)


_NAMED_LAW_OR_ARTICLE = re.compile(
    r"《[^》]{2,60}》|第\s*[0-9０-９零〇一二三四五六七八九十百千]+(?:\s*[之-]\s*"
    r"[0-9０-９零〇一二三四五六七八九十百千]+)?\s*條|"
    r"[\u4e00-\u9fff]{2,16}(?:法|條例|辦法|規則)(?=第|的|網址|條文|資料日期|[，,；;。！？?\s]|$)"
)


_CASE_CONTEXT_CUE = re.compile(
    r"我(?:在|被|遭|遇到|的情況)|本人|當事人|被害人|申訴人|事件|發生|"
    r"主管|同事|教師|學生|學校|公司|職場"
)


def contextless_prior_legal_source_request(message: str) -> bool:
    """Identify a short first-turn request to verify a judgment that is absent."""
    return bool(
        len(message) <= 180
        and _PRIOR_JUDGMENT_SOURCE_REQUEST.search(message)
        and "官方" in message
        and re.search(r"法規|法條|法源|條文|法律", message)
        and re.search(r"網址|連結|來源|條文|原文|資料日期|查核日期", message)
        and not legal_references(message)
        and not _NAMED_LAW_OR_ARTICLE.search(message)
        and not _CASE_CONTEXT_CUE.search(message)
    )


# A bare mention of 條文 is ordinary legal prose. Reserve the whole-unit
# quote safeguard for text that actually introduces or formats a purported
# statutory quotation; otherwise one unsupported cross-reference can erase a
# separately supported article and its citation.
_STATUTORY_QUOTE_CUE = re.compile(
    r"條文(?:原文|如下|內容(?:是|為|如下)|[：:])"
    r"|法條原文|規定(?:如下|[：:])|(?:^|\n)\s*>",
    re.MULTILINE,
)


_JUDGMENT_VERDICT_CUE = re.compile(r"判決處刑如下|判決如下|(?m:^\s*(?:#{1,4}\s*)?主\s*文\s*[:：]?)")


_JUDGMENT_TITLE_CUE = re.compile(r"(?:刑事|民事|行政|法院).{0,25}判決")


_JUDGMENT_PENDING_CUE = re.compile(
    r"(?:尚未|仍未|未經|還沒|目前沒有).{0,8}(?:判決|裁判|審判)"
    r"|(?:尚待|等待|將|未來|後續).{0,12}(?:法院|法官).{0,12}(?:判決|裁判|審判|審理)"
    r"|後續.{0,16}(?:審理結果|判決結果|裁判結果).{0,16}(?:將|待|預計).{0,16}(?:裁決|判決|裁判)"
    r"|(?:目前|仍|尚)(?:在|處於)?(?:偵查|起訴|審理)(?:中|階段)"
)


# Police channels verified against the National Police Agency's 110 reporting
# and public service center pages on 2026-11-10. Avoid invented guarantees
# about any officer's response and keep a hiding person's position private.
_UNSAFE_LOCATION_EXPOSURE = re.compile(
    r"(?:讓|使)(?:對方|行為人|跟蹤者|那個人).{0,12}(?:知道|發現)"
    r".{0,16}(?:在那裡|在這裡|位置|躲在哪|所在)"
)


_POLICE_OFFICER_COMPLAINT_CUE = re.compile(
    r"(?:投訴|陳情)(?:窗口|管道|程序)"
    r"|(?:員警|警員|警方|警察).{0,8}(?:投訴|陳情|服務態度)"
)


_POLICE_NO_DISMISSAL_PROMISE = re.compile(
    r"警方(?:絕對|一定)?不會.{0,90}(?:嘲笑|棄案|不受理|推託|輕忽)"
)


_POLICE_EVIDENCE_PROMISE = re.compile(
    r"警方(?:一定|會).{0,25}(?:保留|蒐集).{0,10}證據"
    r"|警方(?:一定|會).{0,25}持續追蹤"
)


def _correct_police_safety_claim(claim: str) -> tuple[str, str | None]:
    plain = re.sub(r"[*_`]", "", claim)
    prefix = re.match(r"\s*(?:(?:[-*+]|\d+[.)])\s+)?", claim).group()
    if _UNSAFE_LOCATION_EXPOSURE.search(plain) and not re.search(
        r"(?:不要|避免|不必|無須).{0,4}(?:讓|使)(?:對方|行為人|跟蹤者|那個人)", plain
    ):
        return (
            prefix + "若你正在躲避讓你害怕的人，不必為了求助而暴露位置。"
            "可在安全可行時聯絡110、場所工作人員或信任的人，向協助者說明所在位置；"
            "能安全離開時再移動。",
            "unsafe_hiding_location_exposure",
        )
    if "110" in plain and _POLICE_OFFICER_COMPLAINT_CUE.search(plain):
        return (
            prefix + "若要反映員警服務態度，可查詢警政署民眾服務中心或所在地警察局的陳情管道；"
            "110是報案或需要警方協助時的電話。",
            "police_110_miscast_as_complaint_line",
        )
    if _POLICE_NO_DISMISSAL_PROMISE.search(plain):
        return (
            prefix + "你不應因報案而受到嘲笑或責怪；但無法預先保證個別員警的回應或案件處理結果。"
            "若遇到嘲笑、推託或其他服務態度問題，可記錄經過並向警政署民眾服務中心"
            "或所在地警察局的陳情管道反映。",
            "police_response_guarantee",
        )
    if _POLICE_EVIDENCE_PROMISE.search(plain):
        return (
            prefix + "若目前資料有限，仍可向警方說明已知事實，詢問受理方式與可提供的資料；"
            "不必為了蒐集證據而增加自身風險。",
            "police_evidence_guarantee",
        )
    return claim, None


# This correction is bound to the article and reviewed version below. Its
# paraphrases were checked against the MOJ text on 2026-11-10; a later version
# needs a fresh legal review before these rewrites can apply.
_REVIEWED_WORKPLACE_32_1 = {
    "doc_id": "rag_documents/law-N0030014-32-1",
    "law_name": "性別平等工作法",
    "article_number": "32-1",
    "source_url": "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=N0030014&flno=32-1",
    "version": "N0030014@2023-08-16",
    "checked_at": "2026-11-10",
}


_WORKPLACE_PROCESSING_QUALIFIER = re.compile(r"雇主未(?:於|在)合理(?:時間|期間)(?:之)?內?處理")


_WORKPLACE_LONGER_PERIOD = re.compile(
    r"較長(?:的)?(?:申訴)?期限.{0,12}(?:優先|可受理|從其規定)|只要符合較長"
)


_WORKPLACE_EMPLOYER_FIRST = re.compile(
    r"(?:首先\s*(?:應|須|必須|需)?|(?:應|須|必須|需|只能|原則上)?\s*(?:先|首先))"
    r"\s*向雇主(?:提起|提出)?申訴"
)


_WORKPLACE_WRONG_END_ANCHOR = re.compile(
    r"(?:工作關係|勞動關係|雇傭關係|僱傭關係|"
    r"(?:勞動|雇傭|僱傭)契約(?:終止|結束|解除|屆滿)|"
    r"離職|離開(?:職場|公司|工作崗位))"
    r"[^，,。！？!?；;\n]{0,28}(?:五|5|七|7)\s*年"
)


_WORKPLACE_DISJUNCTIVE_LIMIT = re.compile(
    r"(?:二|2)\s*年[^。！？!?\n]{0,45}或[^。！？!?\n]{0,45}(?:五|5)\s*年"
    r"|(?:三|3)\s*年[^。！？!?\n]{0,45}或[^。！？!?\n]{0,45}(?:七|7)\s*年"
)


_WORKPLACE_FIVE_SEVEN_SHORTHAND = re.compile(
    r"(?:五|5)\s*[/／]\s*(?:七|7)\s*年|(?:七|7)\s*[/／]\s*(?:五|5)\s*年"
)


_WORKPLACE_UNSUPPORTED_POWER_GLOSS = re.compile(
    r"(?:權勢地位|具權勢).{0,20}(?:是指|意指|係指|[：:]).{0,25}(?:決策|管理)權限"
    r"|(?:決策|管理)權限.{0,25}(?:包括|包含|例如).{0,25}(?:最高負責人|雇主|主管)"
    r"|實質影響力者"
)


_WORKPLACE_FALSE_POWER_EQUIVALENCE = re.compile(
    r"(?:具)?權勢地位(?:者)?(?:是指|指的是|係指|即|就是|等於)"
    r".{0,22}(?:被申訴人為)?(?:最高負責人|僱用人)"
)


_WORKPLACE_NON_POWER_LIMIT = (
    "被申訴人非具權勢地位時，自知悉性騷擾時起逾二年提起者不予受理；自該行為終了時起逾五年者亦同。"
)


_WORKPLACE_POWER_LIMIT = (
    "被申訴人具權勢地位時，自知悉性騷擾時起逾三年提起者不予受理；自該行為終了時起逾七年者亦同。"
)


_WORKPLACE_32_1_CANONICAL_MARKER = "reviewed_workplace_32_1_canonical_answer"


_WORKPLACE_32_1_CANONICAL = (
    "依《性別平等工作法》第32條之1，受僱者或求職者遭受性騷擾，應向雇主提起申訴；"
    "被申訴人屬最高負責人或僱用人、雇主未處理，或不服被申訴人之雇主所為調查或懲戒結果者，"
    "得逕向地方主管機關提起申訴。\n\n"
    "期限分類看被申訴人是否具權勢地位。被申訴人為最高負責人或僱用人，"
    "另是本條的直接申訴及特別期限條件；具體權勢地位認定須結合相關法條與事件事實核對。\n\n"
    f"{_WORKPLACE_NON_POWER_LIMIT}\n{_WORKPLACE_POWER_LIMIT}\n\n"
    "性騷擾發生時申訴人未成年者，得於成年之日起三年內申訴；"
    "被申訴人為最高負責人或僱用人者，申訴人得於離職之日起一年內申訴，"
    "但自行為終了時起逾十年不予受理。這兩個特別情形不受一般期限限制；"
    "若依前述一般規定有較長申訴期限，從一般規定。\n\n"
    "官方法源：[法務部全國法規資料庫第32條之1]"
    f"({_REVIEWED_WORKPLACE_32_1['source_url']})"
)


def _direct_public_workplace_32_1_query(message: str) -> bool:
    if len(message) > 300 or _CORRECTION_CUE.search(message):
        return False
    if ("性別平等工作法", "32-1") not in legal_references(message):
        return False
    if not all(term in message for term in ("地方主管機關", "權勢", "較長")):
        return False
    return not re.search(
        r"我(?:被|遭|在|的|已|跟|與|是|離職|申訴)|我的|本人|對方|我們|"
        r"去年|今年|事件發生於|當事人是|個案中",
        message,
    )


def _reviewed_workplace_32_1(sources: list[dict]) -> dict | None:
    return next(
        (
            source
            for source in sources
            if all(source.get(key) == value for key, value in _REVIEWED_WORKPLACE_32_1.items())
        ),
        None,
    )


def _correct_reviewed_workplace_power_block(text: str) -> tuple[str, bool]:
    """Replace the observed unsupported Markdown definition and its list."""
    lines = text.splitlines(keepends=True)
    output = []
    changed = False
    index = 0
    while index < len(lines):
        heading = re.sub(r"[#*_`「」『』\s]", "", lines[index])
        heading = re.sub(r"^(?:\d+|[一二三四五六七八九十]+)[.、．]", "", heading)
        heading = heading.rstrip("：:")
        intro_index = index + 1
        while (
            intro_index < len(lines) and not lines[intro_index].strip() and intro_index - index <= 3
        ):
            intro_index += 1
        intro = lines[intro_index] if intro_index < len(lines) else ""
        if not (
            "權勢地位" in heading
            and "定義" in heading
            and len(heading) <= 32
            and "權勢地位" in intro
            and "者指" in intro
            and ("決策" in intro or "管理" in intro)
            and ("包括" in intro or "包含" in intro)
        ):
            output.append(lines[index])
            index += 1
            continue

        end = intro_index + 1
        while end < len(lines) and end - intro_index <= 12:
            line = lines[end]
            stripped = line.strip()
            bullet = bool(re.match(r"(?:[-*+•]|\d+[.)、])\s+", stripped))
            blank_before_bullet = bool(
                not stripped
                and end + 1 < len(lines)
                and re.match(r"(?:[-*+•]|\d+[.)、])\s+", lines[end + 1].strip())
            )
            if bullet or blank_before_bullet:
                end += 1
            elif (
                line.startswith(("  ", "\t")) and stripped and not re.match(r"#{1,6}\s+", stripped)
            ):
                end += 1  # wrapped list item
            else:
                break
        output.append(
            "依性別平等工作法第32條之1，向地方主管機關申訴的期限按被申訴人是否具權勢地位區分；"
            "具體認定須結合相關法條與事件事實核對。\n"
        )
        changed = True
        index = end
    return "".join(output), changed


def _correct_reviewed_workplace_32_1(claim: str) -> tuple[str, list[str]]:
    """Use reviewed wording for observed Article 32-1 semantic slips only."""
    reasons = []
    revised, count = _WORKPLACE_PROCESSING_QUALIFIER.subn("雇主未處理", claim)
    if count:
        reasons.append("unsupported_workplace_processing_qualifier")

    plain = re.sub(r"[*_`「」『』]", "", revised)
    prefix = re.match(r"\s*(?:(?:[-*+]|\d+[.)])\s+)?", claim).group()
    if _WORKPLACE_EMPLOYER_FIRST.search(plain):
        return (
            prefix + "依性別平等工作法第32條之1，一般情形應向雇主提起申訴；"
            "被申訴人屬最高負責人或僱用人、雇主未處理，"
            "或不服被申訴人之雇主所為調查或懲戒結果者，"
            "得逕向地方主管機關提起申訴。",
            [*reasons, "workplace_employer_first_overstated"],
        )
    if _WORKPLACE_FALSE_POWER_EQUIVALENCE.search(plain):
        return (
            prefix + "依性別平等工作法第32條之1，申訴期限按被申訴人是否具權勢地位區分；"
            "被申訴人為最高負責人或僱用人，另是得逕向地方主管機關申訴及適用特別期限的條件。"
            "具體權勢地位認定須結合相關法條與事件事實核對。",
            [*reasons, "workplace_power_status_false_equivalence"],
        )
    if _WORKPLACE_UNSUPPORTED_POWER_GLOSS.search(plain):
        return (
            prefix
            + "依性別平等工作法第32條之1，向地方主管機關申訴的期限按被申訴人是否具權勢地位區分；"
            "具體認定須結合相關法條與事件事實核對。",
            [*reasons, "workplace_power_status_unsupported_gloss"],
        )

    wrong_actor = re.search(
        r"(?<!被)(?:申訴人|受僱者|求職者|受害人|被害人)\s*(?:為|是|屬)?\s*(?:非具|未具|具|非)?權勢",
        plain,
    )
    bare_category = re.search(r"非權勢者|具權勢者", plain) and "被申訴人" not in plain
    unsupported_gloss = re.search(r"非權勢者|具權勢者", plain) and re.search(
        r"(?:非)?主管或高階管理者", plain
    )
    non_power = bool(re.search(r"(?:非|未)(?:具)?權勢", plain))
    power = bool(re.search(r"(?<![非未])具權勢", plain))
    periods = _duration_values(plain)
    non_power_pair = {("2", "年"), ("5", "年")} <= periods
    power_pair = {("3", "年"), ("7", "年")} <= periods
    wrong_end_anchor = bool(_WORKPLACE_WRONG_END_ANCHOR.search(plain))
    five_seven_shorthand = bool(_WORKPLACE_FIVE_SEVEN_SHORTHAND.search(plain))
    mismatched_category = (non_power and not power and power_pair) or (
        power and not non_power and non_power_pair
    )
    if (
        wrong_end_anchor or _WORKPLACE_DISJUNCTIVE_LIMIT.search(plain) or mismatched_category
    ) and not re.search(r"性騷擾防治法|性別平等教育法|性平教育法", plain):
        if wrong_end_anchor and five_seven_shorthand:
            details = [_WORKPLACE_NON_POWER_LIMIT, _WORKPLACE_POWER_LIMIT]
        elif non_power != power:
            details = [_WORKPLACE_NON_POWER_LIMIT if non_power else _WORKPLACE_POWER_LIMIT]
        else:
            details = []
            if non_power_pair or wrong_end_anchor and ("5", "年") in periods:
                details.append(_WORKPLACE_NON_POWER_LIMIT)
            if power_pair or wrong_end_anchor and ("7", "年") in periods:
                details.append(_WORKPLACE_POWER_LIMIT)
        if details:
            correction_reasons = [*reasons, "workplace_deadline_anchor_or_disjunction"]
            if wrong_actor or bare_category or unsupported_gloss:
                correction_reasons.append("workplace_deadline_actor_reversed")
            return prefix + "依性別平等工作法第32條之1，" + "".join(details), correction_reasons

    if wrong_actor or bare_category or unsupported_gloss:
        if non_power and not power:
            detail = _WORKPLACE_NON_POWER_LIMIT
        elif power and not non_power:
            detail = _WORKPLACE_POWER_LIMIT
        else:
            detail = "向地方主管機關申訴的期限依被申訴人是否具權勢地位區分。"
        return prefix + "依性別平等工作法第32條之1，" + detail, [
            *reasons,
            "workplace_deadline_actor_reversed",
        ]

    if (
        ("例外" in plain or "特別情形" in plain)
        and "不受" in plain
        and ("期限" in plain or "限制" in plain)
        and not ("較長" in plain and "從" in plain)
    ):
        return (
            prefix + "依性別平等工作法第32條之1，申訴人於性騷擾發生時未成年，"
            "或被申訴人為最高負責人或僱用人的特別情形，"
            "不受第二項一般期限限制；但依第二項規定有較長申訴期限者，從第二項規定。",
            [*reasons, "workplace_exception_longer_limit_omitted"],
        )

    if _WORKPLACE_LONGER_PERIOD.search(plain) and not re.search(
        r"未成年|最高負責人|僱用人|前項|第二項|前述兩個特別情形", plain
    ):
        return (
            prefix + "依性別平等工作法第32條之1，僅在申訴人於事件時未成年，"
            "或被申訴人為最高負責人或僱用人的特別情形，"
            "若第二項規定有較長申訴期限，從第二項規定。",
            [*reasons, "workplace_longer_period_overgeneralized"],
        )
    return revised, reasons


def _precision_notice(clause: str, reasons: list[str]) -> str | None:
    missing_article = any(
        reason in {"unsupported_article", "unmatched_article", "unmatched_law_article_pair"}
        for reason in reasons
    )
    missing_deadline = any(
        reason
        in {
            "unsupported_deadline_amount",
            "unverified_deadline_version",
            "unsupported_no_deadline_claim",
        }
        for reason in reasons
    )
    missing_version = "unverified_current_version" in reasons
    if not (missing_article or missing_deadline or missing_version):
        return None
    law_names = list(_LAW_REF.finditer(clause))
    law_name = law_names[0].group() if len(law_names) == 1 else ""
    law = f"{law_name}的" if law_name else ""
    if missing_article and missing_deadline:
        notice = f"{law}條號與期限目前沒有可核對的官方來源"
    elif missing_article:
        notice = f"{law}條號目前沒有可核對的官方來源"
    elif missing_deadline:
        notice = f"{law}這項期限目前沒有可核對的官方來源"
    else:
        notice = f"{law_name or '該法規'}目前版本無法核對"
    prefix = re.match(r"\s*(?:(?:[-*+]|\d+[.)])\s+)?", clause)
    suffix = re.search(r"[。！？!?]+$", clause)
    return (prefix.group() if prefix else "") + notice + (suffix.group() if suffix else "")


def _official_source(source: dict) -> bool:
    url = source.get("source_url")
    checked_at = source.get("checked_at")
    if not isinstance(url, str) or not isinstance(checked_at, str):
        return False
    hostname = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https" or not hostname.endswith(".gov.tw"):
        return False
    try:
        date.fromisoformat(checked_at)
    except ValueError:
        return False
    return True


def verified_law_url(source: dict) -> str | None:
    """Expose an official article link only when it agrees with the corpus metadata."""
    if not _official_source(source) or not source.get("law_name"):
        return None
    code, separator, version_date = (source.get("version") or "").partition("@")
    number = _article_number(source.get("article_number") or "")
    url = source["source_url"]
    parsed = urlsplit(url)
    params = parse_qs(parsed.query.lower())
    if (
        not separator
        or not re.fullmatch(r"[A-Z]\d{7}", code)
        or not number
        or parsed.hostname != "law.moj.gov.tw"
        or parsed.path.lower() != "/lawclass/lawsingle.aspx"
        or params.get("pcode") != [code.lower()]
        or params.get("flno") != [number]
    ):
        return None
    try:
        date.fromisoformat(version_date)
    except ValueError:
        return None
    return url


def _source_bound_answer_urls(text, sources, action_urls):
    """Build visible links from approved destinations before producing a stream delta."""
    official = {
        source["source_url"]: source["doc_id"]
        for source in sources
        if (verified_law_url(source) if source.get("type") == "law" else _official_source(source))
    }
    trusted = sorted(set(official).union(action_urls), key=len, reverse=True)
    supported_ids = set()
    unsupported = False

    def replace(match):
        nonlocal unsupported
        raw = match.group("markdown") or match.group("autolink") or match.group("bare")
        suffix = ""
        url = raw if raw in trusted else None
        if url is None and match.group("bare"):
            for candidate in trusted:
                trailing = raw[len(candidate) :]
                if raw.startswith(candidate) and _URL_TEXT_SUFFIX.match(trailing):
                    url, suffix = candidate, trailing
                    break
        if url is None:
            unsupported = True
            return (match.group("label") or "") + "〔官方網址待核對〕"
        if url in official:
            supported_ids.add(official[url])
        if match.group("markdown"):
            return match.group()
        return f"[官方來源](<{url}>)" + suffix

    return _ANSWER_LINK.sub(replace, text), supported_ids, unsupported


def _source_bound_contacts(
    text: str,
    sources: list[dict],
    documents: list[dict],
    *,
    check_urls=True,
    approved_phones=(),
):
    """Check literal phones/URLs against dated, stored official evidence."""
    official = {source["doc_id"]: source for source in sources if _official_source(source)}
    content = {item["source"]["doc_id"]: item["content"] for item in documents}
    supported_ids: set[str] = set()
    unsupported: list[str] = []
    replacements = []
    for match in _URL_REF.finditer(text) if check_urls else ():
        value = match.group()
        host = urlsplit(value).hostname or ""
        candidates = [
            identifier
            for identifier, source in official.items()
            if host.endswith(".gov.tw")
            and (value == source.get("source_url") or value in content.get(identifier, ""))
        ]
        if candidates:
            supported_ids.add(candidates[0])
        else:
            unsupported.append("url")
            replacements.append((*match.span(), "〔官方網址待核對〕"))
    for match in _PHONE_REF.finditer(text):
        number = re.sub(r"\D", "", match.group())
        if number in approved_phones:
            continue
        candidates = [
            identifier
            for identifier in official
            if any(
                re.sub(r"\D", "", candidate.group()) == number
                for candidate in _PHONE_REF.finditer(content.get(identifier, ""))
            )
        ]
        if candidates:
            supported_ids.add(candidates[0])
        else:
            unsupported.append("phone")
            replacements.append((*match.span(), "〔電話待核對〕"))
    for start, end, replacement in sorted(replacements, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text, supported_ids, unsupported


def _has_official_phone_for_agency(agency: str, sources: list[dict], documents: list[dict]) -> bool:
    """A masked claim can only proceed to the model answer if this agency has stored evidence."""
    official = {source["doc_id"]: source for source in sources if _official_source(source)}
    agency_names = {agency, agency.replace("市府", "市政府").replace("縣府", "縣政府")}
    for item in documents:
        source = official.get(item["source"]["doc_id"])
        content = item["content"]
        if (
            source
            and _PHONE_REF.search(content)
            and any(
                name in content or name in str(source.get("label", "")) for name in agency_names
            )
        ):
            return True
    return False


def _law_name(value):
    value = value.strip("《》")
    return _LAW_CANONICAL.get(value, value)


def _source_matches_law(source, law):
    name = source.get("law_name")
    if name:
        return _law_name(str(name)) == law
    return any(
        _law_name(match.group()) == law for match in _LAW_REF.finditer(source.get("label", ""))
    )


def _source_has_article(source, article):
    number = source.get("article_number") or source.get("article")
    if number:
        return _article_number(str(number)) == _article_number(article)
    return any(
        _article_number(item.group()) == _article_number(article)
        for item in _ARTICLE_REF.finditer(source.get("label", ""))
    )


def _calendar_matches(text):
    for match in _CALENDAR_DATE.finditer(text):
        literal = match.group()
        if "年" in literal and "民國" not in literal:
            year = re.search(_NUMBER, literal).group()
            normalized = _article_number(year)
            chinese_year = bool(re.fullmatch(r"[零〇一二三四五六七八九]{4}", year))
            if not (
                chinese_year
                or normalized.isdigit()
                and int(normalized) >= (100 if "月" in literal else 1900)
            ):
                continue  # e.g. 一年六月 is a compound duration, not a year date.
        yield match


def _duration_matches(text):
    # Exclude the entire calendar expression, not just its year. ROC dates,
    # month/day dates, and Chinese-digit years must not become filing periods.
    dates = [match.span() for match in _calendar_matches(text)]
    for match in _DURATION.finditer(text):
        if any(start <= match.start() < end for start, end in dates):
            continue
        number, unit = match.groups()
        normalized = "0.5" if number == "半" else _article_number(number)
        chinese_year = bool(re.fullmatch(r"[零〇一二三四五六七八九]{4}", number))
        if unit == "年" and (chinese_year or normalized.isdigit() and int(normalized) >= 1900):
            continue
        yield match, (normalized, "日" if unit == "天" else unit)


def _duration_values(text):
    return {value for _, value in _duration_matches(text)}


def _deadline_durations(text):
    """Check timing assertions, but not event dates or resource opening hours."""
    candidates = [*_duration_matches(text)]
    candidates.extend(
        (match, ("date", re.sub(r"\s+", "", match.group()))) for match in _calendar_matches(text)
    )
    for match, value in candidates:
        before, after = text[: match.start()], text[match.end() :]
        if (
            re.search(r"(?:期限|時效|截止(?:日)?)[^，,；;。！？!?\n]{0,24}$", before)
            or re.match(
                r"\s*(?:以)?(?:內|前|後)[^，,；;。！？!?\n]{0,20}(?:申訴|告訴|提告|提出|通報|申請|完成)",
                after,
            )
            or (
                re.search(
                    r"(?:申訴|告訴|提告|受理|通報|調查|答辯|申請)[^，,；;。！？!?\n]{0,20}$", before
                )
                and re.match(r"\s*(?:以)?(?:內|前|後)", after)
            )
            or (
                re.search(r"(?:事發|發生|知悉)後[^，,；;。！？!?\n]{0,12}$", before)
                and re.match(r"[^，,；;。！？!?\n]{0,8}(?:提出|申訴|告訴)", after)
            )
            or (
                value[0] == "date"
                and re.search(r"(?:須|應|請|最遲|最晚|必須)(?:於|在)?\s*$", before)
                and re.match(r"[^，,；;。！？!?\n]{0,12}(?:申訴|告訴|提告|提出|通報|申請)", after)
            )
        ):
            yield match, value


def _supported_deadline_value(value, content):
    if value[0] == "date":
        # A publication/event date in a source is not proof of a filing cutoff.
        return value in {candidate for _, candidate in _deadline_durations(content)}
    return value in _duration_values(content)


def check_answer_units(
    units,
    sources: list[dict],
    message: str,
    *,
    content_policy="repair",
    diagnostics=None,
    documents=None,
    seen_reasons=None,
    allow_canonical_public_law=False,
    trusted_action_urls=None,
    approved_phones=(),
) -> list[dict]:
    """Validate cited precision without rewriting the surrounding conversation.

    General legal directions are allowed without citations. Unknown source IDs
    remain contract errors; unsupported article numbers, deadline amounts and
    current-version assertions get local notices, in either policy mode. Raw
    Diagnostics record reason codes without copying draft claims.
    These lexical checks do not establish semantic legal correctness. Paragraph
    context scopes natural references such as 本條 to the preceding law/article.
    A direct public question about the reviewed Article 32-1 can use one
    canonical answer when its caller confirms there is no case context. That
    answer is emitted once across streamed units; other checks retain text.
    """
    by_id = {source["doc_id"]: source for source in sources}
    content_by_id = {doc["source"]["doc_id"]: doc["content"] for doc in documents or []}
    masked_phone = _MASKED_OFFICIAL_PHONE_DECLARATION.fullmatch(message)
    if masked_phone and not _has_official_phone_for_agency(
        masked_phone.group("agency"), sources, documents or []
    ):
        if any(identifier not in by_id for unit in units for identifier in unit.source_ids):
            raise ValueError("Answer references a source outside this retrieval")
        if seen_reasons is not None and _UNVERIFIED_OFFICIAL_PHONE_MARKER in seen_reasons:
            return []
        if seen_reasons is not None:
            seen_reasons.add(_UNVERIFIED_OFFICIAL_PHONE_MARKER)
        if diagnostics is not None:
            diagnostics.setdefault("rewrites", []).append(
                {"section": "answer", "reasons": [_UNVERIFIED_OFFICIAL_PHONE_MARKER]}
            )
        fallback = {
            "direction": "我知道的資訊是：本輪沒有可核對該機關總機電話的官方來源；您說的這個我無法查證。",
            "basis": "本輪沒有可核對該機關總機電話的官方來源。",
            "next_steps": "請以該機關官方網站公布的聯絡資訊為準。",
        }
        return [
            {
                "kind": getattr(unit, "kind", "answer"),
                "text": fallback.get(getattr(unit, "kind", "answer"), fallback["direction"]),
                "source_ids": [],
            }
            for unit in units
        ]
    if (
        allow_canonical_public_law
        and len(units) == 1
        and getattr(units[0], "kind", None) is None
        and _direct_public_workplace_32_1_query(message)
        and (reviewed_source := _reviewed_workplace_32_1(sources))
        and content_by_id.get(reviewed_source["doc_id"])
    ):
        if any(identifier not in by_id for identifier in units[0].source_ids):
            raise ValueError("Answer references a source outside this retrieval")
        if seen_reasons is not None and _WORKPLACE_32_1_CANONICAL_MARKER in seen_reasons:
            return []
        if seen_reasons is not None:
            seen_reasons.add(_WORKPLACE_32_1_CANONICAL_MARKER)
        if diagnostics is not None:
            diagnostics.setdefault("rewrites", []).append(
                {"section": "answer", "reasons": [_WORKPLACE_32_1_CANONICAL_MARKER]}
            )
        return [
            {
                "kind": "answer",
                "text": _WORKPLACE_32_1_CANONICAL,
                "source_ids": [reviewed_source["doc_id"]],
            }
        ]
    correction_about_contact = bool(
        (_CORRECTION_CUE.search(message) or masked_phone)
        and re.search(r"電話|號碼|專線|網址|網頁|連結|網站", message)
    )
    result = []
    for section in units:
        if any(identifier not in by_id for identifier in section.source_ids):
            raise ValueError("Answer references a source outside this retrieval")
        selected = [by_id[identifier] for identifier in section.source_ids]
        parts = []
        retained_ids = set()
        context_law = None
        context_sources = selected
        unsupported_contact = False
        unsupported_article = False
        reviewed_workplace_32_1 = _reviewed_workplace_32_1(selected)
        section_text = section.text
        if trusted_action_urls is not None:
            section_text, link_ids, unverified_link = _source_bound_answer_urls(
                section_text, selected, trusted_action_urls
            )
            retained_ids.update(link_ids)
            if unverified_link and diagnostics is not None:
                diagnostics.setdefault("rewrites", []).append(
                    {"section": getattr(section, "kind", "answer"), "reasons": ["unsupported_url"]}
                )
        if reviewed_workplace_32_1:
            section_text, power_block_rewritten = _correct_reviewed_workplace_power_block(
                section_text
            )
            if power_block_rewritten:
                reason = "workplace_power_status_unsupported_definition_block"
                if seen_reasons is not None:
                    seen_reasons.add(reason)
                if diagnostics is not None:
                    diagnostics.setdefault("rewrites", []).append(
                        {
                            "section": getattr(section, "kind", "answer"),
                            "reasons": [reason],
                        }
                    )
        for claim in _sentences(section_text):
            if is_private_referral_sentence(claim):
                if diagnostics is not None:
                    diagnostics.setdefault("rewrites", []).append(
                        {
                            "section": getattr(section, "kind", "answer"),
                            "reasons": ["private_referral"],
                        }
                    )
                continue
            safer_claim, safety_reason = _correct_police_safety_claim(claim)
            if safety_reason:
                parts.append(safer_claim)
                if seen_reasons is not None:
                    seen_reasons.add(safety_reason)
                if diagnostics is not None:
                    diagnostics.setdefault("rewrites", []).append(
                        {
                            "section": getattr(section, "kind", "answer"),
                            "reasons": [safety_reason],
                        }
                    )
                continue
            if correction_about_contact or (
                trusted_action_urls is not None and _PHONE_REF.search(claim)
            ):
                checked_claim, contact_ids, unsupported = _source_bound_contacts(
                    claim,
                    selected,
                    documents or [],
                    check_urls=trusted_action_urls is None,
                    approved_phones=approved_phones,
                )
                if unsupported:
                    unsupported_contact = True
                    if diagnostics is not None:
                        diagnostics.setdefault("rewrites", []).append(
                            {
                                "section": getattr(section, "kind", "answer"),
                                "reasons": sorted(
                                    set(f"unsupported_{kind}" for kind in unsupported)
                                ),
                            }
                        )
                claim = checked_claim
                retained_ids.update(contact_ids)
            if reviewed_workplace_32_1:
                claim, workplace_reasons = _correct_reviewed_workplace_32_1(claim)
                if workplace_reasons:
                    if seen_reasons is not None:
                        seen_reasons.update(workplace_reasons)
                    if diagnostics is not None:
                        diagnostics.setdefault("rewrites", []).append(
                            {
                                "section": getattr(section, "kind", "answer"),
                                "reasons": sorted(set(workplace_reasons)),
                            }
                        )
            if len(re.findall(r"\r\n|\r|\n", claim)) >= 2:
                context_law, context_sources = None, selected
            # Split only at punctuation already present, retaining it exactly.
            for clause in re.findall(r"[^，,；;]+|[，,；;]", claim):
                repairs = []
                reasons = []
                articles = list(_ARTICLE_REF.finditer(clause))
                laws = list(_LAW_REF.finditer(clause))
                events = [(item.start(), "law", item, None) for item in laws]
                events += [(item.start(), "article", item, None) for item in articles]
                events += [
                    (item.start(), "deadline", item, value)
                    for item, value in _deadline_durations(clause)
                ]
                events += [
                    (item.start(), "no_deadline", item, None)
                    for item in _NO_DEADLINE_CUE.finditer(clause)
                ]
                events += [
                    (item.start(), "current", item, None)
                    for item in _CURRENT_VERSION.finditer(clause)
                ]
                clause_ids = set()
                for _, kind, item, value in sorted(events, key=lambda event: event[0]):
                    if kind == "law":
                        context_law = _law_name(item.group())
                        context_sources = [
                            source
                            for source in selected
                            if _source_matches_law(source, context_law)
                        ]
                    elif kind == "article":
                        candidates = (
                            [
                                source
                                for source in selected
                                if _source_matches_law(source, context_law)
                            ]
                            if context_law
                            else selected
                        )
                        context_sources = [
                            source
                            for source in candidates
                            if _source_has_article(source, item.group())
                        ]
                        if not context_sources:
                            reasons.append(
                                "unmatched_law_article_pair"
                                if context_law and selected
                                else "unmatched_article"
                                if selected
                                else "unsupported_article"
                            )
                            repairs.append((*item.span(), "〔條號待核對〕"))
                        else:
                            clause_ids.update(source["doc_id"] for source in context_sources)
                    elif kind == "deadline":
                        checked = [
                            source
                            for source in context_sources
                            if source.get("version") and source.get("checked_at")
                        ]
                        supported = [
                            source
                            for source in checked
                            if _supported_deadline_value(
                                value, content_by_id.get(source["doc_id"], "")
                            )
                        ]
                        if not supported:
                            reasons.append(
                                "unsupported_deadline_amount"
                                if checked
                                else "unverified_deadline_version"
                            )
                            repairs.append(
                                (
                                    *item.span(),
                                    "〔截止日期待核對〕"
                                    if value[0] == "date"
                                    else "〔期限待核對〕",
                                )
                            )
                        else:
                            clause_ids.update(source["doc_id"] for source in supported)
                    elif kind == "no_deadline":
                        supported = [
                            source
                            for source in context_sources
                            if source.get("version")
                            and source.get("checked_at")
                            and _NO_DEADLINE_CUE.search(content_by_id.get(source["doc_id"], ""))
                        ]
                        if not supported:
                            reasons.append("unsupported_no_deadline_claim")
                        else:
                            clause_ids.update(source["doc_id"] for source in supported)
                    elif kind == "current":
                        # The version adjective often precedes the law name.
                        following = next((law for law in laws if law.start() >= item.end()), None)
                        candidates = (
                            [
                                source
                                for source in selected
                                if _source_matches_law(source, _law_name(following.group()))
                            ]
                            if following
                            else context_sources
                        )
                        if not any(
                            source.get("version") and source.get("checked_at")
                            for source in candidates
                        ):
                            reasons.append("unverified_current_version")
                            repairs.append((*item.span(), "版本待核對的"))
                # A missing number must not leave a categorical case verdict.
                if repairs:
                    for verdict in re.finditer(
                        r"(?:一定|必然|確定|已經)(?:構成|成立|違法|適用)", clause
                    ):
                        repairs.append((*verdict.span(), "是否適用仍需個案判斷"))
                elif clause.strip() and not articles:
                    clause_ids.update(source["doc_id"] for source in context_sources)
                revised = clause
                for start, end, replacement in sorted(repairs, reverse=True):
                    revised = revised[:start] + replacement + revised[end:]
                notice = _precision_notice(clause, reasons)
                if notice is not None:
                    # A marker embedded in the original sentence still reads
                    # like a statutory claim. Drop that clause and its citation.
                    revised = notice
                    clause_ids.clear()
                parts.append(revised)
                retained_ids.update(clause_ids)
                if reasons:
                    unsupported_article = unsupported_article or any(
                        reason
                        in {
                            "unsupported_article",
                            "unmatched_article",
                            "unmatched_law_article_pair",
                        }
                        for reason in reasons
                    )
                    if seen_reasons is not None:
                        seen_reasons.update(reasons)
                    if diagnostics is not None:
                        diagnostics.setdefault("rewrites", []).append(
                            {
                                "section": getattr(section, "kind", "answer"),
                                "reasons": sorted(set(reasons)),
                            }
                        )
        revised_text = "".join(parts)
        if unsupported_article and _STATUTORY_QUOTE_CUE.search(section.text):
            # Replacing the article number alone can leave a fabricated block
            # that still looks like the law's verbatim wording. No part of this
            # unit should be presented as the requested statutory text.
            revised_text = "目前沒有可核對該條號與條文內容的官方來源，無法確認或提供其條文原文。"
            retained_ids.clear()
            if diagnostics is not None:
                diagnostics.setdefault("rewrites", []).append(
                    {
                        "section": getattr(section, "kind", "answer"),
                        "reasons": ["unverified_statutory_quote"],
                    }
                )
        if unsupported_contact and correction_about_contact:
            revised_text = (
                revised_text.rstrip()
                + "\n我知道的資訊是目前缺少可核對的官方來源；您說的這個我無法查證。"
            )
        if reviewed_workplace_32_1 and re.search(r"官方(?:法源|來源)|法源(?:連結|網址)", message):
            url = _REVIEWED_WORKPLACE_32_1["source_url"]
            link_marker = "reviewed_workplace_32_1_official_link"
            if url in revised_text:
                if seen_reasons is not None:
                    seen_reasons.add(link_marker)
            elif seen_reasons is None or link_marker not in seen_reasons:
                revised_text = (
                    revised_text.rstrip() + f"\n官方法源：[法務部全國法規資料庫第32條之1]({url})"
                )
                retained_ids.add(reviewed_workplace_32_1["doc_id"])
                if seen_reasons is not None:
                    seen_reasons.add(link_marker)
        if len(selected) == 1 and selected[0].get("type") == "judgment":
            judgment_content = content_by_id.get(selected[0]["doc_id"], "")
            if _JUDGMENT_VERDICT_CUE.search(judgment_content) or _JUDGMENT_TITLE_CUE.search(
                selected[0].get("label", "")
            ):
                repaired = []
                for sentence in _sentences(revised_text):
                    if _JUDGMENT_PENDING_CUE.search(sentence):
                        sentence = (
                            "已檢索到的判決有裁判結果，不能稱本案仍待法院裁判；請以裁判原文核對。"
                        )
                        if diagnostics is not None:
                            diagnostics.setdefault("rewrites", []).append(
                                {
                                    "section": getattr(section, "kind", "answer"),
                                    "reasons": ["judgment_outcome_contradicts_source"],
                                }
                            )
                    repaired.append(sentence)
                revised_text = "".join(repaired)
        if not revised_text.strip():
            revised_text = "可向適用的官方受理窗口詢問協助方式。"
        result.append(
            {
                "kind": getattr(section, "kind", "answer"),
                "text": revised_text,
                "source_ids": [
                    identifier for identifier in section.source_ids if identifier in retained_ids
                ],
            }
        )
    return result
