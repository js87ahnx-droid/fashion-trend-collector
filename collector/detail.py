"""글 1건의 상세 — Claude Sonnet이 원문을 읽고 핵심·업계에 주는 의미·보강 설명·그래픽 명세를 쓴다.
brief 글(원문을 열지 않는 매체)은 도구 없이 제목·매체·RSS 요약만으로 한 줄 소개를 쓴다."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from . import classify

log = logging.getLogger("collector")

MODEL = os.environ.get("ANTHROPIC_DETAIL_MODEL", "claude-sonnet-5-5")   # 2026-11-13 사용자 결정(비용)
WORKERS = 4
WEB_FETCH = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 1, "max_content_tokens": 4000}
MAX_CONTINUE = 3

STYLE = """문체:
- 독자는 국내 패션 회사 직장인(기획·MD·마케팅·영업)이다. 업계 말은 알지만 해외 기사(영어·프랑스어·이탈리아어)·금융 용어엔
  익숙하지 않을 수 있다. 원문이 외국어여도 모든 칸을 한국어로 쓴다.
  "~해요/~합니다"체, 짧은 문장.
- 회사·브랜드명·숫자·기간(분기 등)은 정확히 살린다. 금액은 원문 통화 그대로 쓰고 원화로 바꾸지 않는다.
- 어려운 말(EBITDA, 홀세일 등)은 쉬운 말로 바꾸거나, 꼭 필요하면 그대로 쓰고 terms에서 풀이한다.
"""

PROMPT = """너는 국내 패션 회사 직장인을 위한 업계 뉴스 큐레이터다. 아래 기사의 원문을 web_fetch로 읽고
(실패하면 '수집된 본문'만 사용) 독자가 원문을 열지 않고도 무슨 일인지, 우리 일에 무슨 뜻인지 알 수 있게 한국어로 정리하라.

{style}
규칙:
- insufficient: 원문을 읽지 못했고 수집된 본문만으로는 핵심 3가지를 사실대로 쓸 수 없으면 true
  (그때 나머지 필드는 비워도 된다). 그 외엔 false.
- 원문 문장을 그대로 옮기지 말고 직접 요약한다. 직접 인용 금지.
- 원문에 없는 사실·숫자를 지어내지 않는다. 확인 못 한 부분은 쓰지 않는다.
- headline: 원문을 읽고 우리 헤드라인이 틀렸거나 과장됐으면 고친 헤드라인(한국어 45자 이내), 맞으면 그대로.
- points: 무슨 일인지 핵심 3개, 각 한 문장 (80자 이내)
- so_what: 이 소식이 국내 패션 회사에 무엇을 뜻하는지 1~3개, 각 120자 이내. 원문에 근거가 있는 범위에서만,
  "~할 수 있어요"처럼 단정하지 않는 말로. 근거가 없으면 빈 배열.
- extra: 맥락·배경 1~3문장, 300자 이내. 없으면 "".
- figure: 이해를 확실히 돕는 경우에만 하나, 아니면 type="none".
  bars=원문에 있는 수치 비교(실적·점유율 등, bars 2~6개, unit), compare=전/후 또는 A/B 비교(before·after 각 70자 이내,
  라벨 각 12자 이내), flow=단계 흐름(steps 2~5개, 각 12자 이내), quote=인물의 핵심 주장 요지(직접 인용이 아닌 요지, speaker).
  쓰지 않는 필드는 빈 문자열·빈 배열·0으로.
- terms: 본문(headline·points·so_what·extra)에 남은 어려운 말 0~3개.
  term은 용어 그대로, meaning은 한 문장 풀이(60자 이내). 없으면 빈 배열.

기사:
- 제목: {title}
- 매체: {outlet}
- 우리 헤드라인: {headline}
- 카테고리: {cat}
- 원문: {url}
- 수집된 본문: {body}
"""

BRIEF_PROMPT = """너는 국내 패션 회사 직장인을 위한 업계 뉴스 큐레이터다. 아래 기사는 원문을 열 수 없다(유료이거나 수집을 막은 매체).
아래 '근거'(제목·매체·요약)만 보고 무슨 소식인지 한국어로 짧게 소개하라.

{style}
규칙:
- 근거에 없는 숫자·이유·인명·회사명·전망을 쓰지 않는다. 추측하지 않는다.
- insufficient: 근거만으로 무슨 소식인지 한 문장도 사실대로 쓸 수 없으면 true. 그 외엔 false.
- headline: 근거에 맞는 한국어 헤드라인(45자 이내). 우리 헤드라인이 근거와 맞으면 그대로.
- points: 근거가 말하는 내용 한 문장(80자 이내) 1개만.
- so_what·extra·terms는 비우고, figure는 type="none".

근거:
- 제목: {title}
- 매체: {outlet}
- 요약: {body}
- 우리 헤드라인: {headline}
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "insufficient": {"type": "boolean"},
        "headline": {"type": "string"},
        "points": {"type": "array", "items": {"type": "string"}},
        "so_what": {"type": "array", "items": {"type": "string"}},
        "extra": {"type": "string"},
        "figure": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["none", "compare", "flow", "bars", "quote"]},
                "title": {"type": "string"},
                "before": {"type": "string"},
                "after": {"type": "string"},
                "before_label": {"type": "string"},
                "after_label": {"type": "string"},
                "steps": {"type": "array", "items": {"type": "string"}},
                "unit": {"type": "string"},
                "bars": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}, "value": {"type": "number"}},
                        "required": ["label", "value"],
                        "additionalProperties": False,
                    },
                },
                "text": {"type": "string"},
                "speaker": {"type": "string"},
            },
            "required": ["type", "title", "before", "after", "before_label", "after_label",
                         "steps", "unit", "bars", "text", "speaker"],
            "additionalProperties": False,
        },
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["term", "meaning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insufficient", "headline", "points", "so_what", "extra", "figure", "terms"],
    "additionalProperties": False,
}


class Insufficient(classify.LLMError):
    """정리할 근거가 부족한 글 — 다시 시도해도 같으므로 건너뛴 글로 기억한다."""


def write(item: dict, client, usage: classify.Usage | None = None) -> dict:
    """item에 detail을 붙여 반환. 서버 도구가 pause_turn으로 멈추면 이어서 요청(글 1건 = 상세 1회로 센다)."""
    body = (item.get("body") or item.get("excerpt") or "(없음)")[:3000]
    if item.get("brief"):        # 원문 페이지를 열지 않는다 — 도구 없음
        prompt = BRIEF_PROMPT.format(style=STYLE, title=item["title"], outlet=item.get("outlet", ""), body=body,
                                     headline=item["headline"])
        tools = {}
    else:
        prompt = PROMPT.format(style=STYLE, title=item["title"], outlet=item.get("outlet", ""), headline=item["headline"],
                               cat=item["cat"], url=item["url"], body=body)
        tools = {"tools": [WEB_FETCH]}
    messages = [{"role": "user", "content": prompt}]
    if usage:
        usage.count("detail_items")
    for _ in range(MAX_CONTINUE):
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            **tools,
            messages=messages,
        )
        if usage:
            usage.add(resp)
        if resp.stop_reason != "pause_turn":
            data = classify.final_json(resp)
            if data.pop("insufficient", False):
                raise Insufficient("원문을 읽지 못했고 본문이 부족함")
            headline = (data.pop("headline", "") or "").strip()
            fixed = {"headline": headline[:60]} if headline else {}
            return item | fixed | {"detail": data}
        messages = messages + [{"role": "assistant", "content": resp.content}]
    raise classify.LLMError("pause_turn 반복 — 상세 작성 중단")


def write_all(items: list[dict], client, usage: classify.Usage | None = None
              ) -> tuple[list[dict], list[str], list[str], list[str]]:
    """여러 글을 병렬로. (성공 목록, 실패 사유, 근거 부족 id, 실패 id).
    실패한 글은 다음 회차에 다시 시도되고(2번 실패하면 호출한 쪽이 건너뛴 글로 기억), 근거 부족 글은 바로 기억한다."""
    def one(it):
        try:
            return write(it, client, usage), None
        except Insufficient:
            log.info("근거 부족으로 건너뜀 %s", it["id"])
            return None, ("insufficient", it["id"])
        except Exception as e:
            log.warning("상세 실패 %s: %s", it["id"], e)
            return None, ("failed", it["id"], f"{it['id']}: {type(e).__name__}: {e}"[:200])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, items))
    ok = [r for r, _ in results if r]
    errors = [e[2] for _, e in results if e and e[0] == "failed"]
    failed = [e[1] for _, e in results if e and e[0] == "failed"]
    thin = [e[1] for _, e in results if e and e[0] == "insufficient"]
    return ok, errors, thin, failed
