"""Claude Code(`claude -p`)를 Anthropic SDK 대신 쓰는 클라이언트 — Max 구독 토큰(CLAUDE_CODE_OAUTH_TOKEN)으로 돈다.

분류·상세 코드가 쓰는 `client.beta.messages.create(...)` 모양을 그대로 흉내 내서, 응답을 SDK 응답처럼 돌려준다.
- 한 번의 요청 = `claude -p` 한 번(프롬프트는 표준 입력). 결과는 `--json-schema`의 structured_output.
- 도구: 상세(원문 읽기)만 WebFetch 하나, 그 밖엔 도구 없음(`--tools ""`). 빈 임시 폴더에서 실행해 파일을 볼 수 없다.
- ANTHROPIC_API_KEY가 환경에 있으면 Claude Code가 그 키를 먼저 쓰므로, 자식 프로세스 환경에서 뺀다.
2026-11-14 사용자 결정. 구독 약관(자동화 접근) 위험은 사용자에게 알렸다 — implementation-notes 참고.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

TIMEOUT = 300          # 초 — 원문 읽기까지 포함한 한 번의 요청
SYSTEM = ("너는 국내 패션 회사 직장인을 위한 업계 뉴스 큐레이터다. 사용자 메시지의 지시를 따르고, "
          "WebFetch가 주어지면 지시된 원문 주소만 연다(그 주소가 다른 주소로 넘어간다고 알려 오면 넘어간 주소를 한 번 더 열어도 된다). "
          "결과는 요청한 JSON 형식으로만 낸다.")


class ClaudeCodeError(Exception):
    pass


class ClaudeCodeClient:
    def __init__(self, exe: str | None = None, run=subprocess.run, timeout: int = TIMEOUT):
        self.exe = exe or shutil.which("claude") or "claude"
        self._run = run
        self.timeout = timeout
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def args(self, model: str, schema: dict, web: bool) -> list[str]:
        # Claude Code 기본 시스템 프롬프트(코딩 도우미용, 수만 토큰)를 짧은 것으로 바꿔 구독 사용량을 아낀다.
        return [self.exe, "-p", "--output-format", "json", "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--model", model, "--permission-mode", "dontAsk", "--system-prompt", SYSTEM,
                "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                "--tools", "WebFetch" if web else "", *(["--allowedTools", "WebFetch"] if web else [])]

    def create(self, *, model: str, messages: list[dict], output_config: dict, tools: list | None = None, **_ignored):
        if len(messages) != 1 or not isinstance(messages[0].get("content"), str):
            raise ClaudeCodeError("claude -p 클라이언트는 한 번의 사용자 메시지만 보낸다")
        schema = output_config["format"]["schema"]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}   # 구독 토큰을 쓰게
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:
            try:
                p = self._run(self.args(model, schema, bool(tools)), input=messages[0]["content"], capture_output=True,
                              text=True, encoding="utf-8", timeout=self.timeout, cwd=cwd, env=env)
            except subprocess.TimeoutExpired as e:
                raise ClaudeCodeError(f"claude -p {self.timeout}초 안에 안 끝남") from e
        try:
            out = json.loads(p.stdout)
        except (json.JSONDecodeError, TypeError) as e:
            raise ClaudeCodeError(f"claude -p 출력이 JSON이 아님 (exit {p.returncode}): {(p.stderr or p.stdout or '')[:300]}") from e
        if out.get("is_error") or p.returncode != 0:
            raise ClaudeCodeError(f"claude -p 실패: {str(out.get('result') or out.get('subtype') or p.stderr)[:300]}")
        if out.get("structured_output") is None:
            raise ClaudeCodeError(f"structured_output 없음: {str(out.get('result'))[:200]}")
        u = out.get("usage") or {}
        used_models = list((out.get("modelUsage") or {}).keys())
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(out["structured_output"], ensure_ascii=False))],
            model=used_models[0] if len(used_models) == 1 else model,
            cost_usd=float(out.get("total_cost_usd") or 0),
            usage=SimpleNamespace(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                  cache_creation_input_tokens=u.get("cache_creation_input_tokens", 0),
                                  cache_read_input_tokens=u.get("cache_read_input_tokens", 0),
                                  server_tool_use=SimpleNamespace(
                                      web_fetch_requests=(u.get("server_tool_use") or {}).get("web_fetch_requests", 0))),
        )
