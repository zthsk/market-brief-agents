import json

from services.gemini import generate_json


class _Response:
    def __init__(self, text: str):
        self._text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}


def test_generate_json_sends_system_instruction_and_json_mode(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _Response('{"ok": true}')

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr("services.gemini.requests.post", fake_post)

    payload = generate_json({"ticker": "ADBE"}, "Return JSON only.")

    request_json = calls[0]["json"]
    contents = json.loads(request_json["contents"][0]["parts"][0]["text"])
    assert payload == {"ok": True}
    assert contents == {
        "payload": {"ticker": "ADBE"},
        "format": "Return a single valid JSON object. Do not wrap it in Markdown.",
    }
    assert request_json["system_instruction"]["parts"][0]["text"] == "Return JSON only."
    assert request_json["generationConfig"]["responseMimeType"] == "application/json"
