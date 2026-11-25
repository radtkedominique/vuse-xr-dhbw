"""Tests für judge.py ohne echte API-Aufrufe (OpenRouter wird gemockt)."""
import json

import pytest

from vuse_xr.cap3d import judge
from vuse_xr.cap3d.eval_data import Caption, Part

PART = Part(part_id="04_torque_wrench__u7", asset="04_torque_wrench", asset_label="torque wrench", kind="mesh",
            node="Ratchet_Head", names=("Ratchet_Head", "Ratchet_Head_Hull"), path="Body/Head_Assembly",
            rel_size=0.2, n_meshes=None)
CAP = Caption(model="gemini-3.8-flash", variant="hier", part_id=PART.part_id,
              caption={"name": "ratchet head housing", "type": "housing", "function": "holds the ratchet",
                       "description": "round head", "confidence": 0.87},
              meta={"model": "gemini-3.8-flash"})
GOOD = {"reason": "Same part.", "category": "correct", "score": 2, "function_ok": True,
        "hallucination": False, "gt_unusable": False}
PROMPT = judge.Prompt("v1", "system text", "abc123")
JC = {"retries": 0, "invalid_retries": 1}


def response(content, model="vendor/judge-0601", cost=0.0001):
    return {"id": "gen-1", "model": model, "provider": "Vendor",
            "choices": [{"message": {"content": content if isinstance(content, str) else json.dumps(content)}}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 60, "cost": cost,
                      "completion_tokens_details": {"reasoning_tokens": 0}}}


@pytest.fixture
def api(monkeypatch):
    """Mock für OpenRouter: Antworten der Reihe nach, mitgeschnittene Request-Bodies."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    calls, answers = [], []

    def fake_http(url, body, headers, timeout):
        calls.append(body)
        return answers.pop(0)

    monkeypatch.setattr(judge, "http", fake_http)
    return calls, answers


def test_input_has_gt_but_no_model_variant_confidence():
    text = judge.judge_input(PART, CAP.caption)
    assert "Correct name(s): Ratchet_Head  (also: Ratchet_Head_Hull)" in text
    assert "Position in tree: Body/Head_Assembly" in text and "Object: torque wrench" in text
    for leak in ("gemini", "hier", "u7", "0.87", "confidence"):
        assert leak not in text


def test_input_group_and_empty_fields():
    group = Part(**{**PART.__dict__, "kind": "group", "n_meshes": 6, "names": ("Head",), "path": ""})
    text = judge.judge_input(group, {"name": "head", "type": None})
    assert "Kind: sub-assembly of 6 parts" in text and "(top level)" in text and "type: (empty)" in text


def test_cache_key_changes_with_prompt_and_judge():
    user = judge.judge_input(PART, CAP.caption)
    k = judge.cache_key(user, PROMPT, "a/b")
    assert k == judge.cache_key(user, PROMPT, "a/b")
    assert k != judge.cache_key(user, judge.Prompt("v1", "edited", "def456"), "a/b")
    assert k != judge.cache_key(user, PROMPT, "a/c")
    assert k != judge.cache_key(user + "x", PROMPT, "a/b")


@pytest.mark.parametrize("patch, ok", [
    ({}, True),
    ({"score": 1}, False),                                    # correct muss 2 sein
    ({"category": "wrong_function", "score": 1}, True),
    ({"category": "wrong_function", "score": 2}, False),
    ({"category": "neighbor_part", "score": 1}, True),
    ({"category": "richtig"}, False),
    ({"score": True}, False),                                 # bool ist kein Score
    ({"function_ok": "yes"}, False),
    ({"reason": " "}, False),
])
def test_validate(patch, ok):
    assert (judge.validate({**GOOD, **patch}) is None) == ok


def test_validate_missing_field():
    assert "reason" in judge.validate({k: v for k, v in GOOD.items() if k != "reason"})


def test_parse_content_strips_fences():
    assert judge.parse_content('```json\n{"a": 1}\n```') == {"a": 1}


def test_request_body():
    b = judge.request_body("sys", "usr", "x/y", {"reasoning": {"effort": "minimal"}})
    assert b["temperature"] == 0 and b["response_format"]["json_schema"]["strict"] is True
    assert b["provider"] == {"require_parameters": True} and b["reasoning"] == {"effort": "minimal"}
    assert "reasoning" not in judge.request_body("s", "u", "x/y", {"reasoning": None})


def test_judge_one_writes_cache_and_reuses_it(tmp_path, api):
    calls, answers = api
    answers.append(response(GOOD))
    r = judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path)
    f = tmp_path / "vendor_judge" / "gemini-3.8-flash" / "hier" / f"{PART.part_id}.json"
    saved = json.loads(f.read_text())
    assert saved["judgement"] == GOOD and r["key"] == saved["key"]
    assert saved["meta"]["judge_model"] == "vendor/judge-0601"       # ausgelieferte Version
    assert saved["meta"]["tokens"] == {"input": 900, "output": 60, "reasoning": 0}
    assert saved["meta"]["cost_usd"] == 0.0001

    again = judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path)
    assert again["_cached"] and len(calls) == 1
    answers.append(response(GOOD))
    judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path, force=True)
    assert len(calls) == 2


def test_new_caption_invalidates_cache(tmp_path, api):
    calls, answers = api
    answers += [response(GOOD), response({**GOOD, "category": "other_part", "score": 0})]
    judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path)
    changed = Caption(**{**CAP.__dict__, "caption": {**CAP.caption, "name": "handle"}})
    r = judge.judge_one(PART, changed, PROMPT, "vendor/judge", JC, tmp_path)
    assert len(calls) == 2 and r["judgement"]["score"] == 0


def test_invalid_answer_retried_once_then_fails(tmp_path, api):
    calls, answers = api
    answers += [response("kein json"), response({**GOOD, "score": 0})]
    with pytest.raises(RuntimeError, match="ungültige Antwort"):
        judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path)
    assert len(calls) == 2 and not list(tmp_path.rglob("*.json"))


def test_invalid_then_valid_sums_cost(tmp_path, api):
    _, answers = api
    answers += [response("", cost=0.001), response(GOOD, cost=0.002)]
    r = judge.judge_one(PART, CAP, PROMPT, "vendor/judge", JC, tmp_path)
    assert r["meta"]["cost_usd"] == pytest.approx(0.003)


def test_select_skips_empty_names_and_filters_cases():
    parts = {PART.part_id: PART}
    empty = Caption(**{**CAP.__dict__, "variant": "baseline", "caption": {"name": " "}})
    other = Caption(**{**CAP.__dict__, "part_id": "x__unknown"})
    todo, n_empty = judge.select(parts, [CAP, empty, other], None, None)
    assert todo == [CAP] and n_empty == 1
    assert judge.select(parts, [CAP], None, {(PART.part_id, "gemini-3.8-flash", "baseline")})[0] == []
    assert judge.select(parts, [CAP], None, {(PART.part_id,)})[0] == [CAP]


def test_prompt_v1_exists_and_lists_all_categories():
    p = judge.load_prompt("v1")
    for cat in judge.SCORES:
        assert cat in p.text


def test_daily_limit_stops_without_retries(tmp_path, api, monkeypatch):
    import io
    import urllib.error
    calls, answers = api

    def fake_http(url, body, headers, timeout):
        calls.append(body)
        raise urllib.error.HTTPError(url, 429, "x", {}, io.BytesIO(b"Rate limit exceeded: free-models-per-day"))

    monkeypatch.setattr(judge, "http", fake_http)
    with pytest.raises(judge.BudgetExceeded, match="Tageslimit"):
        judge.judge_one(PART, CAP, PROMPT, "vendor/judge", {**JC, "retries": 3}, tmp_path)
    assert len(calls) == 1
