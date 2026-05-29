"""
測試：PII 匿名化模組
"""

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
