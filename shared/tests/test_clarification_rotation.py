"""Synthetic historical signatures exercise derived-key rotation and expiry."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from rag_core.core import clarification_tokens as tokens

OLD_SECRET = "synthetic-former-signing-secret"
NEW_SECRET = "synthetic-independent-signing-secret"
NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


def derived_key(secret, version):
    return hashlib.sha256(f"clarification-v{version}:{secret}".encode()).digest()


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def rotation(monkeypatch):
    settings = SimpleNamespace(
        clarification_signing_key=NEW_SECRET,
        clarification_legacy_signing_key=json.dumps(
            {str(version): derived_key(OLD_SECRET, version).hex() for version in (3, 4)}
        ),
        clarification_legacy_valid_until="2026-10-10T12:00:01Z",
    )
    monkeypatch.setattr(tokens, "get_settings", lambda: settings)
    monkeypatch.setattr(tokens, "datetime", FixedDatetime)
    monkeypatch.delenv("CLARIFICATION_SIGNING_KEY", raising=False)
    return settings


def payload(version):
    result = {
        "question_id": "synthetic-question",
        "selection_mode": "single",
        "max_selections": 1,
        "context_scope": "personal",
        "allowed_values": ["fixture choice"],
    }
    if version == 3:
        result["fact_key"] = "relationship"
    else:
        result.update(contract_version=4, context_revision=7)
    return result


def signed_payload(value, key):
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(key, encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def answer(version, token):
    result = {
        "question_id": "synthetic-question",
        "validation_token": token,
        "status": "provided",
        "value": "fixture choice",
    }
    if version == 3:
        result["fact_key"] = "relationship"
    else:
        result["context_revision"] = 7
    return result


@pytest.mark.parametrize("version", [3, 4])
def test_derived_legacy_key_verifies_issued_token_before_expiry(rotation, version):
    issued = payload(version)
    token = signed_payload(issued, derived_key(OLD_SECRET, version))
    assert (
        tokens.validate_clarification_answer(answer(version, token), contract_version=version)
        == issued
    )
    assert OLD_SECRET not in rotation.clarification_legacy_signing_key


@pytest.mark.parametrize("version", [3, 4])
@pytest.mark.parametrize(
    "expires",
    [
        None,
        "",
        "not-a-date",
        "2026-10-10T12:00:01",
        "2026-10-10T12:00:00Z",
        "2026-10-10T11:59:59+00:00",
    ],
)
def test_old_token_is_rejected_at_expiry_or_without_valid_aware_deadline(
    rotation, version, expires
):
    rotation.clarification_legacy_valid_until = expires
    token = signed_payload(payload(version), derived_key(OLD_SECRET, version))
    with pytest.raises(ValueError, match="issued question"):
        tokens.validate_clarification_answer(answer(version, token), contract_version=version)


@pytest.mark.parametrize("version", [3, 4])
def test_new_questions_always_use_new_key_and_work_after_legacy_expiry(rotation, version):
    question = payload(version)
    question["options"] = [{"value": "fixture choice"}]
    token = tokens.sign_clarification(question, contract_version=version)
    encoded, signature = token.rsplit(".", 1)
    assert (
        signature
        == hmac.new(derived_key(NEW_SECRET, version), encoded.encode(), hashlib.sha256).hexdigest()
    )
    assert (
        signature
        != hmac.new(derived_key(OLD_SECRET, version), encoded.encode(), hashlib.sha256).hexdigest()
    )
    rotation.clarification_legacy_valid_until = "2026-10-09T12:00:00Z"
    assert tokens.validate_clarification_answer(
        answer(version, token), contract_version=version
    ) == payload(version)


@pytest.mark.parametrize(
    "legacy",
    [
        OLD_SECRET,
        "not-json",
        "[]",
        "null",
        "{}",
        '{"3": null}',
        '{"3": 23}',
        '{"3":"abc"}',
        json.dumps({"3": "z" * 64}),
        json.dumps({"4": derived_key(OLD_SECRET, 3).hex()}),
    ],
)
def test_malformed_or_wrong_version_legacy_secret_fails_closed(rotation, legacy):
    rotation.clarification_legacy_signing_key = legacy
    token = signed_payload(payload(3), derived_key(OLD_SECRET, 3))
    with pytest.raises(ValueError, match="issued question"):
        tokens.validate_clarification_answer(answer(3, token), contract_version=3)


@pytest.mark.parametrize("version", [3, 4])
def test_contracts_do_not_share_verification_keys(rotation, version):
    other = 4 if version == 3 else 3
    token = signed_payload(payload(version), derived_key(OLD_SECRET, other))
    with pytest.raises(ValueError, match="issued question"):
        tokens.validate_clarification_answer(answer(version, token), contract_version=version)


def test_even_a_valid_v4_signature_cannot_authorize_a_v3_payload(rotation):
    malformed = payload(3)
    malformed.update(contract_version=4, context_revision=7)
    token = signed_payload(malformed, derived_key(OLD_SECRET, 4))
    with pytest.raises(ValueError, match="issued question"):
        tokens.validate_clarification_answer(answer(4, token), contract_version=4)


def test_v4_legacy_token_still_binds_context_revision(rotation):
    token = signed_payload(payload(4), derived_key(OLD_SECRET, 4))
    changed = answer(4, token) | {"context_revision": 8}
    with pytest.raises(ValueError, match="issued question"):
        tokens.validate_clarification_answer(changed, contract_version=4)


def test_process_fallback_does_not_reuse_model_or_admin_credentials(rotation, monkeypatch):
    rotation.clarification_signing_key = None
    monkeypatch.setenv("ADMIN_API_KEY", OLD_SECRET)
    monkeypatch.setenv("OPENROUTER_API_KEY", OLD_SECRET)
    assert tokens._key(3) != derived_key(OLD_SECRET, 3)
    assert tokens._key(3) == tokens._key(3)
    assert tokens._key(3) != tokens._key(4)
