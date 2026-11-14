"""Claude Code(`claude -p`) 클라이언트 — 실제 실행 없이 가짜 subprocess로."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from collector import cc_client, classify, detail

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def fake(stdout: dict | str, code=0, calls=None):
    def run(args, **kw):
        if calls is not None:
            calls.append((args, kw))
        return SimpleNamespace(stdout=stdout if isinstance(stdout, str) else json.dumps(stdout), stderr="", returncode=code)
    return run


OK = {"type": "result", "is_error": False, "result": "", "structured_output": {"a": "가"}, "total_cost_usd": 0.0123,
      "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5},
      "modelUsage": {"claude-sonnet-5-5": {}}}


def test_args_tools_and_env_without_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    calls = []
    c = cc_client.ClaudeCodeClient(exe="claude", run=fake(OK, calls=calls))
    msgs = [{"role": "user", "content": "프롬프트"}]
    fmt = {"format": {"type": "json_schema", "schema": SCHEMA}}
    c.beta.messages.create(model="claude-opus-5-5", messages=msgs, output_config=fmt, max_tokens=10, fallbacks="default")
    c.beta.messages.create(model="claude-sonnet-5-5", messages=msgs, output_config=fmt, tools=[{"type": "web_fetch"}])
    (a1, kw1), (a2, _) = calls
    assert a1[:2] == ["claude", "-p"] and a1[a1.index("--tools") + 1] == "" and "--allowedTools" not in a1   # 분류: 도구 없음
    assert a2[a2.index("--tools") + 1] == "WebFetch" and a2[a2.index("--allowedTools") + 1] == "WebFetch"     # 상세: WebFetch만
    assert a1[a1.index("--model") + 1] == "claude-opus-5-5" and json.loads(a1[a1.index("--json-schema") + 1]) == SCHEMA
    assert a1[a1.index("--permission-mode") + 1] == "dontAsk"
    assert a1[a1.index("--system-prompt") + 1] == cc_client.SYSTEM                                         # 기본 프롬프트 대체
    assert {"--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"} <= set(a1)
    assert kw1["input"] == "프롬프트" and "ANTHROPIC_API_KEY" not in kw1["env"]


def test_response_shaped_like_sdk_and_usage_recorded():
    c = cc_client.ClaudeCodeClient(exe="claude", run=fake(OK))
    resp = c.beta.messages.create(model="claude-sonnet-5-5", messages=[{"role": "user", "content": "x"}],
                                  output_config={"format": {"schema": SCHEMA}})
    assert classify.final_json(resp) == {"a": "가"}
    u = classify.Usage()
    u.add(resp)
    assert u.data["by_model"]["claude-sonnet-5-5"] == {"input_tokens": 105, "output_tokens": 20}
    assert u.data["cost_usd"] == 0.0123


@pytest.mark.parametrize("stdout,code,msg", [
    ({**OK, "is_error": True, "result": "Credit balance is too low"}, 1, "Credit balance"),
    ({**OK, "structured_output": None}, 0, "structured_output 없음"),
    ("not json", 1, "JSON이 아님"),
])
def test_errors_raise(stdout, code, msg):
    c = cc_client.ClaudeCodeClient(exe="claude", run=fake(stdout, code))
    with pytest.raises(cc_client.ClaudeCodeError, match=msg):
        c.beta.messages.create(model="m", messages=[{"role": "user", "content": "x"}], output_config={"format": {"schema": SCHEMA}})


def test_timeout_raises():
    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)
    c = cc_client.ClaudeCodeClient(exe="claude", run=slow)
    with pytest.raises(cc_client.ClaudeCodeError, match="안 끝남"):
        c.beta.messages.create(model="m", messages=[{"role": "user", "content": "x"}], output_config={"format": {"schema": SCHEMA}})


def test_make_client_picks_by_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    assert isinstance(classify.make_client(), cc_client.ClaudeCodeClient) and classify.has_credentials()
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert not classify.has_credentials()


def test_detail_write_through_claude_code_client():
    """상세 작성 경로가 claude -p 응답으로 끝까지 돈다(도구는 WebFetch)."""
    d = {"insufficient": False, "headline": "고친 헤드라인", "points": ["하나", "둘", "셋"], "so_what": [], "extra": "",
         "figure": {"type": "none"}, "terms": []}
    calls = []
    c = cc_client.ClaudeCodeClient(exe="claude", run=fake({**OK, "structured_output": d}, calls=calls))
    item = {"id": "f-1", "title": "t", "url": "https://x.com/1", "headline": "h", "cat": "brand", "outlet": "WWD"}
    out = detail.write(item, c)
    assert out["headline"] == "고친 헤드라인" and out["detail"]["points"] == ["하나", "둘", "셋"]
    assert "WebFetch" in calls[0][0]
