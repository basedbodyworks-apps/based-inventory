"""Tests for Telegram HTTP fallback."""

from unittest.mock import MagicMock

from based_inventory.telegram import TelegramFallback


def test_send_calls_bot_api(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"ok": True}))
    monkeypatch.setattr("based_inventory.telegram.requests.post", mock_post)

    tg = TelegramFallback(bot_token="12345:abc", chat_id="-100123")
    ok = tg.send("hello")

    assert ok is True
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bot12345:abc/sendMessage"
    assert kwargs["json"]["chat_id"] == "-100123"
    assert kwargs["json"]["text"] == "hello"


def test_send_no_op_when_not_configured(monkeypatch):
    mock_post = MagicMock()
    monkeypatch.setattr("based_inventory.telegram.requests.post", mock_post)

    tg = TelegramFallback(bot_token=None, chat_id=None)
    ok = tg.send("hello")

    assert ok is True  # not an error, just skipped
    mock_post.assert_not_called()


def test_send_handles_api_error(monkeypatch):
    mock_post = MagicMock(
        return_value=_mock_response({"ok": False, "description": "chat not found"})
    )
    monkeypatch.setattr("based_inventory.telegram.requests.post", mock_post)

    tg = TelegramFallback(bot_token="12345:abc", chat_id="bad")
    ok = tg.send("hello")

    assert ok is False


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response
