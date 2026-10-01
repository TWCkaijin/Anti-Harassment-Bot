"""
測試：PII 匿名化模組
"""

import pytest

from backend.app.core.anonymizer import anonymize, anonymize_messages


def test_anonymize_mobile_phone():
    result = anonymize("我的電話是 0912-345-678")
    assert "[手機號碼]" in result.anonymized
    assert result.was_modified
    assert "taiwan_mobile" in result.detected_types


def test_anonymize_email():
    result = anonymize("聯絡我：victim@example.com")
    assert "[電子郵件]" in result.anonymized
    assert "email" in result.detected_types


def test_anonymize_taiwan_id():
    result = anonymize("我的身份證是 A123456789")
    assert "[身份證號]" in result.anonymized
    assert "taiwan_id" in result.detected_types


def test_no_pii_unchanged():
    text = "我想了解性騷擾防治法的申訴流程"
    result = anonymize(text)
    assert result.anonymized == text
    assert not result.was_modified
    assert result.detected_types == []


def test_anonymize_messages_only_user():
    messages = [
        {"role": "user", "content": "我叫 victim@example.com"},
        {"role": "assistant", "content": "我了解，請繼續說"},
        {"role": "user", "content": "我的電話 0912345678"},
    ]
    result = anonymize_messages(messages)
    assert result[0]["role"] == "user"
    assert "[電子郵件]" in result[0]["content"]
    # assistant 訊息不應被修改
    assert result[1]["content"] == "我了解，請繼續說"
    assert "[手機號碼]" in result[2]["content"]


@pytest.mark.parametrize(
    "text",
    [
        "事件日期：20260930-20261001；我現在不安全。",
        "事發日期為20240229 20240301。",
        "請參考 1.2.3.4 小節。",
        "章節：1.2.3.4，記錄處理程序。",
        "版本 1.2.3.4 的文件。",
        "女主管遭男部屬試圖環抱，尚未發生接觸。",
        "我剛剛在公車上被人摸屁股了，我該怎麼辦？",
        "性別平等工作法第12條、性騷擾防治法第25條，事件在2026-10-01。",
        "2026/10/01、2026年10月1日、民國115年10月1日；對方正在跟蹤我。",
    ],
)
def test_preserves_legal_context_dates_and_document_numbers(text):
    result = anonymize(text)
    assert result.anonymized == text
    assert result.detected_types == []


@pytest.mark.parametrize(
    "text,identifier,kind,placeholder",
    [
        ("我的手機0912345678請勿外傳", "0912345678", "taiwan_mobile", "[手機號碼]"),
        ("電話(02)1234-5678請勿公開", "(02)1234-5678", "taiwan_phone", "[電話號碼]"),
        ("請寄信victim@example.com給我", "victim@example.com", "email", "[電子郵件]"),
        ("我的身分證A123456789請保密", "A123456789", "taiwan_id", "[身份證號]"),
        ("卡號4111111111111111請保密", "4111111111111111", "credit_card", "[信用卡號]"),
        ("IP位址192.0.2.1請保密", "192.0.2.1", "ipv4", "[IP位址]"),
        ("小節記載 IP：1.2.3.4。", "1.2.3.4", "ipv4", "[IP位址]"),
        ("信用卡：20260930-20261001", "20260930-20261001", "credit_card", "[信用卡號]"),
        ("識別碼：20260230-20260301", "20260230-20260301", "credit_card", "[信用卡號]"),
    ],
)
def test_masks_identifiers_next_to_chinese_and_keeps_explicit_pii_context(
    text, identifier, kind, placeholder
):
    result = anonymize(text)
    assert identifier not in result.anonymized
    assert placeholder in result.anonymized
    assert kind in result.detected_types


def test_removing_identifiers_preserves_case_roles_actions_place_time_and_safety():
    text = (
        "我是女主管，對方是男部屬；2026-10-01在公車被摸屁股，我現在不安全。"
        "聯絡電話0912345678；信箱test@example.com。"
    )
    result = anonymize(text)
    for fact in ("女主管", "男部屬", "2026-10-01", "公車", "被摸屁股", "不安全"):
        assert fact in result.anonymized
    assert set(result.detected_types) == {"taiwan_mobile", "email"}
    assert anonymize(result.anonymized).anonymized == result.anonymized
