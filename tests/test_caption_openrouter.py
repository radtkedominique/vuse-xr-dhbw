"""Tests für das OpenRouter-Backend in caption.py und --model-id, ohne echte API-Aufrufe."""
import base64
import io
import json
import urllib.error

import pytest

from vuse_xr.cap3d import caption
from vuse_xr.cap3d.caption import SCHEMA, QuotaExceeded, apply_model_id, call_openrouter, call_with_retry

PNG = b"\x89PNG fake"
CC = {"backend": "openrouter", "model": "qwen/qwen3-vl-235b-a22b-instruct", "retries": 0, "timeout": 5}
CAP = {"name": "rotor lid", "type": "lid", "function": "covers the rotor", "description": "round", "confidence": 0.7}


def response(content, **extra):
    return {"id": "gen-9", "model": "qwen/qwen3-vl-235b-a22b-instruct-0723", "provider": "Alibaba",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2300, "completion_tokens": 80, "cost": 0.0006,
                      "completion_tokens_details": {"reasoning_tokens": 0}}, **extra}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls, answers = [], []

    def fake_post(url, body, headers, timeout):
        calls.append((url, body, headers))
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(caption, "post_json", fake_post)
    return calls, answers


def http_error(code, msg):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(msg.encode()))


def test_body_images_schema_and_params(api):
    calls, answers = api
    answers.append(response(json.dumps(CAP)))
    call_openrouter("prompt", [PNG, PNG], {**CC, "reasoning": {"effort": "low"}})
    url, body, headers = calls[0]
    assert url == caption.OPENROUTER_URL and headers["Authorization"] == "Bearer test"
    content = body["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "prompt"} and len(content) == 3
    assert content[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["required"]) == set(SCHEMA["properties"])
    assert body["temperature"] == 0 and body["reasoning"] == {"effort": "low"}
    assert body["provider"] == {"require_parameters": True} and "max_tokens" not in body


def test_temperature_null_is_omitted(api):
    calls, answers = api
    answers.append(response(json.dumps(CAP)))
    call_openrouter("p", [PNG], {**CC, "temperature": None})
    assert "temperature" not in calls[0][1] and "reasoning" not in calls[0][1]


def test_usage_has_model_version_and_cost(api):
    _, answers = api
    answers.append(response("```json\n" + json.dumps(CAP) + "\n```"))
    cap, usage = call_openrouter("p", [PNG], CC)
    assert cap == CAP
    assert usage == {"input": 2300, "output": 80, "thinking": 0, "cost": 0.0006, "provider": "Alibaba",
                     "model_version": "qwen/qwen3-vl-235b-a22b-instruct-0723", "generation_id": "gen-9"}


@pytest.mark.parametrize("resp, match", [
    ({"error": {"message": "provider down"}}, "OpenRouter"),
    (response(None), "leere Antwort"),
])
def test_errors(api, resp, match):
    _, answers = api
    answers.append(resp)
    with pytest.raises(RuntimeError, match=match):
        call_openrouter("p", [PNG], CC)


@pytest.mark.parametrize("code, msg", [
    (402, "Insufficient credits"),
    (429, "Rate limit exceeded: free-models-per-day"),
])
def test_quota_stops_run(api, code, msg):
    _, answers = api
    answers.append(http_error(code, msg))
    with pytest.raises(QuotaExceeded):
        call_with_retry("openrouter", "p", [PNG], CC)


def test_apply_model_id():
    models = {"M2": {"name": "Qwen3", "backend": "openrouter", "model": "qwen/x", "price": [1, 2],
                     "reasoning": None, "temperature": None}}
    cc = apply_model_id({"backend": "gemini", "model": "g", "input_views": "part"}, models, "M2")
    assert cc == {"backend": "openrouter", "model": "qwen/x", "input_views": "part", "reasoning": None,
                  "temperature": None, "model_id": "M2"}
    with pytest.raises(SystemExit):
        apply_model_id({}, models, "M9")


def test_models_yaml_entries_are_usable():
    from vuse_xr.cap3d.render import ROOT, load_cfg
    models = load_cfg(ROOT / "configs" / "models.yaml")["models"]
    for mid, m in models.items():
        assert m["backend"] in caption.BACKENDS, mid
        assert m["model"], mid
