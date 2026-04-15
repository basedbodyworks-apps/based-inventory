"""Tests for Slack Block Kit poster."""

from unittest.mock import MagicMock

from based_inventory.slack import SlackClient


def test_post_message_success(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"ok": True, "ts": "123.456"}))
    monkeypatch.setattr("based_inventory.slack.requests.post", mock_post)

    client = SlackClient(token="xoxb-test", channel="C123")
    ok = client.post_message(fallback_text="hi", blocks=[{"type": "section"}])

    assert ok is True
    args, kwargs = mock_post.call_args
    assert args[0] == "https://slack.com/api/chat.postMessage"
    body = kwargs["json"]
    assert body["channel"] == "C123"
    assert body["text"] == "hi"
    assert body["blocks"] == [{"type": "section"}]
    assert body["unfurl_links"] is False


def test_post_message_returns_false_on_not_ok(monkeypatch):
    mock_post = MagicMock(return_value=_mock_response({"ok": False, "error": "channel_not_found"}))
    monkeypatch.setattr("based_inventory.slack.requests.post", mock_post)

    client = SlackClient(token="xoxb-test", channel="C123")
    ok = client.post_message(fallback_text="hi", blocks=[])

    assert ok is False


def test_dry_run_does_not_call_api(monkeypatch, capsys):
    mock_post = MagicMock()
    monkeypatch.setattr("based_inventory.slack.requests.post", mock_post)

    client = SlackClient(token="xoxb-test", channel="C123", dry_run=True)
    ok = client.post_message(fallback_text="hi", blocks=[{"type": "section"}])

    assert ok is True
    mock_post.assert_not_called()
    captured = capsys.readouterr()
    assert "[DRY_RUN]" in captured.out
    assert "hi" in captured.out


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response
