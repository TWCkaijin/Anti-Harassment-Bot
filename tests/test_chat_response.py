"""Validate action selectors separately from trusted UI configuration."""

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.core.chat_response import (
    AssistantActionButton,
    AssistantChatResponse,
    action_key,
    action_selector,
    configured_action_adapter,
)

selector_adapter = TypeAdapter(AssistantActionButton)


def test_response_accepts_all_three_generic_selectors():
    selectors = [
        {"action": "tel", "phone_number": "113"},
        {"action": "url", "url": "https://www.pthg.gov.tw/"},
        {"action": "options", "id": "choose_next_step"},
    ]
    response = AssistantChatResponse.model_validate(
        {
            "emotion": "平靜",
            "emotion_color": "green",
            "reply": "請選擇需要的協助。",
            "suggested_replies": ["我想了解流程", "我想整理資料"],
            "action_buttons": selectors,
        }
    )

    assert [action_selector(action) for action in response.action_buttons] == selectors


@pytest.mark.parametrize(
    "selector",
    [
        {"action": "url", "url": "https://example.com", "label": "模型編造"},
        {"action": "options", "id": "next_step", "options": []},
        {"action": "options", "id": "INVALID"},
        {"action": "options", "id": "a"},
        {"action": "tel", "phone_number": "javascript:alert(1)"},
        {"action": "other", "id": "some_action"},
    ],
)
def test_model_cannot_inject_ui_configuration_or_invalid_selectors(selector):
    with pytest.raises(ValidationError):
        selector_adapter.validate_python(selector)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,bad",
        "//example.com",
        "https:///missing-host",
        "https://user:secret@example.com/",
        "https://example.com\n.evil.test/",
        "https://exa mple.com/",
        "https://example.com\\@evil.test/",
        "https://-bad.test/",
        "https://example.com:99999/",
        "https://[invalid]/",
        " https://example.com/",
    ],
)
def test_invalid_urls_are_rejected_in_selectors_and_configuration(url):
    with pytest.raises(ValidationError):
        selector_adapter.validate_python({"action": "url", "url": url})
    with pytest.raises(ValidationError):
        configured_action_adapter.validate_python({"action": "url", "url": url, "label": "網站"})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/path?a=1#part",
        "https://www.pthg.gov.tw/",
        "https://[::1]/",
        "https://例子.台灣/",
    ],
)
def test_valid_http_urls_preserve_destination(url):
    action = selector_adapter.validate_python({"action": "url", "url": url})
    assert action.url == url


def _options(**changes):
    return {
        "action": "options",
        "id": "some_choices",
        "label": "選擇",
        "title": "想先了解什麼？",
        "options": [
            {"label": "流程", "value": "我想了解流程"},
            {"label": "資料", "value": "我想整理資料"},
        ],
        **changes,
    }


def test_model_selector_does_not_include_options_or_labels():
    configured = configured_action_adapter.validate_python(_options())
    assert action_selector(configured) == {"action": "options", "id": "some_choices"}
    assert action_key(configured) == ("options", "some_choices")
    assert action_key(configured.model_dump()) == ("options", "some_choices")


@pytest.mark.parametrize(
    "changes",
    [
        {"label": " "},
        {"label": "x" * 81},
        {"title": "x" * 161},
        {"options": []},
        {"options": [{"label": "one", "value": "one"}]},
        {"options": [{"label": str(i), "value": str(i)} for i in range(9)]},
        {"options": [{"label": "one", "value": ""}, {"label": "two", "value": "two"}]},
        {"options": [{"label": "one", "value": "x" * 501}, {"label": "two", "value": "two"}]},
        {"options": [{"label": "one", "value": "same"}, {"label": "two", "value": "same"}]},
    ],
)
def test_options_configuration_requires_bounded_distinct_choices(changes):
    with pytest.raises(ValidationError):
        configured_action_adapter.validate_python(_options(**changes))


@pytest.mark.parametrize("value", [None, [], {}, {"action": "unknown"}, {"action": "url"}])
def test_malformed_action_keys_are_ignored(value):
    assert action_key(value) is None
