import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from collector import classify, detail, main, store
from collector.sources.base import raw

NOW = datetime(2026, 10, 30, 12, 37, tzinfo=timezone.utc)
DETAIL = {"points": ["핵심 하나", "핵심 둘", "핵심 셋"], "so_what": ["업계 의미"], "extra": "",
          "figure": {"type": "flow", "title": "", "before": "", "after": "", "before_label": "", "after_label": "",
                     "steps": ["입력", "출력"], "unit": "", "bars": [], "text": "", "speaker": ""}}


def msg(payload, stop_reason="end_turn"):
    text = json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])


class FakeClient:
    """분류 요청과 상세 요청을 응답 형식(상세엔 insufficient 칸)으로 구분해 응답한다. brief 글 상세는 도구 없이 온다."""

    def __init__(self, classify_fn=None, detail_fn=None):
        self.classify_fn = classify_fn or (lambda kw: {"results": []})
        self.detail_fn = detail_fn or (lambda kw: msg(DETAIL))
        self.calls = []
        self.lock = threading.Lock()
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        with self.lock:
            self.calls.append(kw)
        if is_detail(kw):
            return self.detail_fn(kw)
        out = self.classify_fn(kw)
        return out if isinstance(out, SimpleNamespace) else msg(out)

    def classify_calls(self):
        return [c for c in self.calls if not is_detail(c)]


def is_detail(kw):
    return "insufficient" in kw["output_config"]["format"]["schema"]["properties"]


def r(id_, title, source="feed", region="global", outlet="WWD", **kw):
    return raw(id=id_, title=title, url=f"https://ex.com/{id_}", source=source, region=region, outlet=outlet, **kw)


def ids_in(kw):
    return [json.loads(l)["id"] for l in kw["messages"][0]["content"].splitlines() if l.startswith("{")]


def label_all(cat="brand", pick=5, replaces=""):
    return lambda kw: {"results": [{"id": i, "skip": False, "cat": cat, "headline": f"{i} 헤드라인", "tag": "5분",
                                    "why": "이유", "pick": pick, "replaces": replaces} for i in ids_in(kw)]}


def test_prefilter_drops_only_ads():
    """S1 AC⑦ — 업계 기사에 흔한 낱말은 남기고 광고 문구만 버린다."""
    cands = [
        r("f-1", "이마트 할인점 매출 3분기 감소"),
        r("f-2", "Brand X wholesale 확대"),
        r("f-3", "연말 이벤트 매출 2배"),
        r("f-4", "쿠폰 받기 — 오늘만"),
        r("f-5", "Best leggings, promo code inside"),
        r("f-6", "쿠팡 쿠폰 정책 변경, 판매자 부담 늘어"),
        r("f-7", "Holiday sales slow at department stores"),
    ]
    assert [c["id"] for c in classify.prefilter(cands)] == ["f-1", "f-2", "f-3", "f-6", "f-7"]


def test_classify_filters_and_sorts_by_pick():
    cands = [r("hn-1", "a"), r("hn-2", "b"), r("hn-3", "c"), r("hn-4", "d")]
    res = [
        {"id": "hn-1", "skip": False, "cat": "retail", "headline": "무료 CLI", "tag": "무료", "why": "w", "pick": 4, "replaces": "x-9"},
        {"id": "hn-2", "skip": True, "cat": "none", "headline": "", "tag": "", "why": "", "pick": 0, "replaces": ""},
        {"id": "hn-3", "skip": False, "cat": "model", "headline": "h", "tag": "t", "why": "", "pick": 9, "replaces": ""},
        {"id": "hn-4", "skip": False, "cat": "brand", "headline": "팁", "tag": "", "why": "", "pick": 15, "replaces": "gn-1"},
        {"id": "hn-9", "skip": False, "cat": "brand", "headline": "없는 id", "tag": "", "why": "", "pick": 5, "replaces": ""},
    ]
    client = FakeClient(lambda kw: {"results": res})
    stored = [{"id": "gn-1", "cat": "brand", "headline": "예전 팁", "brief": True}]
    out, skipped = classify.classify(cands, client, stored)
    assert [(o["id"], o["pick"], o["replaces"]) for o in out] == [("hn-4", 10, "gn-1"), ("hn-1", 4, None)]
    assert skipped == ["hn-2", "hn-3"]                     # 응답에 없는 id는 어느 쪽에도 없음
    assert out[0]["tag"] == "참고"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "gn-1 | brand | true | 예전 팁" in prompt and client.calls[0]["fallbacks"] == "default"


def test_classify_excludes_superseded_from_stored():
    client = FakeClient()
    classify.classify([r("hn-1", "a")], client, [{"id": "gn-1", "cat": "brand", "headline": "옛 팁", "superseded_by": "x"}])
    assert "옛 팁" not in client.calls[0]["messages"][0]["content"]


def test_refusal_raises():
    with pytest.raises(classify.LLMError):
        classify.classify([r("hn-1", "a")], FakeClient(lambda kw: msg({"results": []}, "refusal")))


def test_detail_uses_web_fetch_and_continues_after_pause():
    seq = [msg({}, "pause_turn"), msg(DETAIL)]
    client = FakeClient(detail_fn=lambda kw: seq.pop(0))
    item = r("hn-1", "t") | {"headline": "h", "cat": "brand"}
    out = detail.write(item, client)
    assert out["detail"]["points"][0] == "핵심 하나"
    first, second = client.calls
    assert first["tools"][0]["type"] == "web_fetch_20260209"
    assert "https://ex.com/hn-1" in first["messages"][0]["content"]
    assert second["messages"][-1]["role"] == "assistant"


def test_detail_can_correct_headline():
    item = r("hn-1", "t") | {"headline": "2.1.312·313 점검", "cat": "brand"}
    out = detail.write(item, FakeClient(detail_fn=lambda kw: msg(DETAIL | {"headline": "2.1.313 점검"})))
    assert out["headline"] == "2.1.313 점검" and "headline" not in out["detail"]
    same = detail.write(item, FakeClient(detail_fn=lambda kw: msg(DETAIL | {"headline": " "})))
    assert same["headline"] == "2.1.312·313 점검"


def test_detail_failure_isolated():
    def det(kw):
        if "hn-2" in kw["messages"][0]["content"]:
            raise RuntimeError("fetch 실패")
        return msg(DETAIL)

    items = [r(f"hn-{i}", "t") | {"headline": "h", "cat": "brand"} for i in (1, 2, 3)]
    ok, errs, thin = detail.write_all(items, FakeClient(detail_fn=det))
    assert [o["id"] for o in ok] == ["hn-1", "hn-3"] and "hn-2" in errs[0] and thin == []


def test_detail_insufficient_reported_separately():
    def det(kw):
        if "hn-2" in kw["messages"][0]["content"]:
            return msg(DETAIL | {"insufficient": True})
        return msg(DETAIL | {"insufficient": False})

    items = [r(f"hn-{i}", "t") | {"headline": "h", "cat": "brand"} for i in (1, 2)]
    ok, errs, thin = detail.write_all(items, FakeClient(detail_fn=det))
    assert [o["id"] for o in ok] == ["hn-1"] and errs == [] and thin == ["hn-2"]
    assert "insufficient" not in ok[0]["detail"]


class Boom:
    @staticmethod
    def fetch(ctx):
        raise RuntimeError("소스 다운")


class Good:
    @staticmethod
    def fetch(ctx):
        return [r("hn-10", "Claude agent memory tips", score=50),
                r("hn-11", "Gardening news"),
                r("hn-12", "GPT prompt trick", score=5)]


def test_run_end_to_end(tmp_path, monkeypatch):
    old = {"id": "hn-1", "title": "old", "url": "https://ex.com/old", "source": "feed", "region": "global",
           "outlet": "WWD", "cat": "brand", "headline": "옛",
           "tag": "t", "detail": DETAIL, "collected_at": store.iso(NOW - timedelta(days=8))}
    store.save_json(tmp_path / "items.json", store.items_doc([old]))
    client = FakeClient(label_all())
    st = main.run(NOW, tmp_path, client, sources=[("boom", Boom), ("feeds", Good)])

    items = store.load_items(tmp_path / "items.json")
    assert sorted(i["id"] for i in items) == ["hn-10", "hn-11", "hn-12"]   # 8일 지난 글 삭제
    it = items[0]
    assert it["collected_at"] == st["last_run"] and it["detail"]["figure"]["type"] == "flow"
    assert "body" not in it and "excerpt" not in it
    assert "소스 다운" in st["sources"]["boom"]["error"]
    assert st["sources"]["feeds"] == {"fetched": 3, "kept": 3}             # source=feed → feeds로 묶여 셈
    assert len(client.classify_calls()) == 1 and len(client.calls) == 4      # 분류 1 + 상세 3


def test_run_replacement_marks_old(tmp_path):
    """S4 AC③ — 저장된 brief 글을 원문을 읽은 새 글이 replaces로 대체."""
    class BriefFirst:
        @staticmethod
        def fetch(ctx):
            return [r("hn-10", "Burberry sales firm in China", brief=True)]

    one = {"points": ["제목 기준 한 줄"], "so_what": [], "extra": "", "figure": None, "terms": []}
    main.run(NOW, tmp_path, FakeClient(label_all(), lambda kw: msg(one)), sources=[("feeds", BriefFirst)])

    class Newer:
        @staticmethod
        def fetch(ctx):
            return [r("hn-20", "Better Claude agent memory approach")]

    main.run(NOW + timedelta(hours=1), tmp_path, FakeClient(label_all(replaces="hn-10")), sources=[("feeds", Newer)])
    items = {i["id"]: i for i in store.load_items(tmp_path / "items.json")}
    assert items["hn-10"]["superseded_by"] == "hn-20" and items["hn-20"]["replaces"] == "hn-10"


def test_run_without_key_still_updates_last_run(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    st = main.run(NOW, tmp_path, None, sources=[("feeds", Good)])
    assert st["last_run"] == "2026-10-30T12:37:00Z"
    assert "ANTHROPIC_API_KEY" in st["run"]["note"]
    assert store.load_items(tmp_path / "items.json") == []


def test_skipped_ids_remembered_and_expire(tmp_path):
    def judge(kw):
        return {"results": [{"id": i, "skip": i == "hn-12", "cat": "none" if i == "hn-12" else "brand",
                             "headline": "h", "tag": "t", "why": "", "pick": 1, "replaces": ""} for i in ids_in(kw)]}

    main.run(NOW, tmp_path, FakeClient(judge), sources=[("feeds", Good)])
    mem = json.loads((tmp_path / "skipped.json").read_text(encoding="utf-8"))
    assert list(mem) == ["hn-12"]
    client = FakeClient()
    main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("feeds", Good)])
    assert client.calls == []                                # 건너뛴 글을 다시 보내지 않음
    client = FakeClient(judge)
    main.run(NOW + timedelta(days=9), tmp_path, client, sources=[("feeds", Good)])
    assert ids_in(client.calls[0]) == ["hn-12"]              # 8일 지나면 기억에서 빠짐
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert "skipped" not in st


def test_second_run_skips_known(tmp_path):
    main.run(NOW, tmp_path, FakeClient(label_all()), sources=[("feeds", Good)])
    client = FakeClient()
    st = main.run(NOW + timedelta(hours=1), tmp_path, client, sources=[("feeds", Good)])
    assert client.calls == [] and st["run"]["added"] == 0


def test_buzz_and_terms_saved_and_prompts_ask_plain_language(tmp_path, monkeypatch):

    def labels(kw):
        out = label_all()(kw)
        out["results"][0]["buzz"] = True
        return out

    terms = [{"term": "MCP", "meaning": "AI에 외부 자료를 꽂아 쓰게 하는 연결 방식이에요"}]
    client = FakeClient(labels, lambda kw: msg(DETAIL | {"terms": terms}))
    main.run(NOW, tmp_path, client, sources=[("feeds", Good)])
    items = {i["id"]: i for i in store.load_items(tmp_path / "items.json")}
    assert items["hn-10"]["buzz"] is True and "buzz" not in items["hn-12"]
    assert items["hn-10"]["detail"]["terms"] == terms
    cls = client.classify_calls()[0]["messages"][0]["content"]
    det = [c for c in client.calls if c.get("tools")][0]["messages"][0]["content"]
    assert "패션 회사" in cls and "brief" in cls and "buzz" in cls
    assert "terms" in det and "~해요/~합니다" in det
    assert "buzz" in classify.CLASSIFY_SCHEMA["properties"]["results"]["items"]["required"]
    assert "terms" in detail.SCHEMA["required"]


def test_status_lists_every_outlet(tmp_path, monkeypatch):
    """S1 AC⑩ — status.sources.feeds.outlets에 FEEDS의 모든 매체가 나온다(0건 매체 포함)."""
    from collector import http
    from collector.sources import feeds

    monkeypatch.setattr(feeds, "FEEDS", (feeds.Feed("a", "https://a/feed", "A매체", "kr", 3, timedelta(days=7)),
                                         feeds.Feed("b", "https://b/feed", "B매체", "global", 3, timedelta(days=7))))
    monkeypatch.setattr(http, "get_text", lambda url, **kw: "<rss><channel></channel></rss>")
    st = main.run(NOW, tmp_path, None)
    assert st["sources"]["feeds"]["outlets"] == {"A매체": {"fetched": 0}, "B매체": {"fetched": 0}}



# --- S4: 분류 ---

def test_prompt_carries_brief_values_and_replace_rule():
    """S4 AC② — 후보·저장 글의 brief 값과 'brief 글은 replaces' 문구가 프롬프트에 들어간다."""
    client = FakeClient()
    classify.classify([r("g-1", "BoF title", source="gnews", brief=True), r("f-2", "WWD title")], client,
                      [{"id": "s-1", "cat": "brand", "headline": "저장 brief", "brief": True},
                       {"id": "s-2", "cat": "retail", "headline": "저장 일반"}])
    prompt = client.calls[0]["messages"][0]["content"]
    lines = {json.loads(l)["id"]: json.loads(l) for l in prompt.splitlines() if l.startswith("{")}
    assert lines["g-1"]["brief"] is True and lines["f-2"]["brief"] is False
    assert lines["g-1"]["outlet"] == "WWD" and lines["g-1"]["region"] == "global"
    assert "s-1 | brand | true | 저장 brief" in prompt and "s-2 | retail | false | 저장 일반" in prompt
    assert "저장된 글이 brief=true이고 이 글이 brief=false면 skip 대신 replaces" in prompt


def test_new_brief_replacing_is_skipped_not_saved(tmp_path):
    """S4 AC②-2 — 새 brief 글이 저장된 글을 replaces로 가리키면 저장하지 않고 건너뛴 글로 기억."""
    main.run(NOW, tmp_path, FakeClient(label_all()), sources=[("feeds", Good)])

    class BriefDup:
        @staticmethod
        def fetch(ctx):
            return [r("g-9", "Same story via BoF", source="gnews", brief=True)]

    main.run(NOW + timedelta(hours=3), tmp_path, FakeClient(label_all(replaces="hn-10")), sources=[("gnews", BriefDup)])
    items = {i["id"]: i for i in store.load_items(tmp_path / "items.json")}
    assert "g-9" not in items and "superseded_by" not in items["hn-10"]
    assert "g-9" in json.loads((tmp_path / "skipped.json").read_text(encoding="utf-8"))


def test_normal_item_cannot_replace_normal_item():
    """대체는 저장된 brief 글만 — 일반 글끼리는 replaces를 버린다."""
    client = FakeClient(label_all(replaces="s-2"))
    out, _ = classify.classify([r("f-3", "x")], client, [{"id": "s-2", "cat": "retail", "headline": "일반"}])
    assert out[0]["replaces"] is None


def test_brief_never_picked(tmp_path):
    """S4 AC④ — 가짜 응답이 brief 글에 높은 pick을 줘도 저장본엔 pick 없음."""
    class Mixed:
        @staticmethod
        def fetch(ctx):
            return [r("g-1", "BoF scoop", source="gnews", brief=True), r("f-1", "WWD story")]

    one = {"points": ["한 줄"], "so_what": [], "extra": "", "figure": None, "terms": []}
    client = FakeClient(label_all(pick=9), lambda kw: msg(one if "g-1" in kw["messages"][0]["content"] else DETAIL))
    main.run(NOW, tmp_path, client, sources=[("feeds", Mixed)])
    items = {i["id"]: i for i in store.load_items(tmp_path / "items.json")}
    assert "pick" not in items["g-1"] and items["g-1"]["brief"] is True
    assert items["f-1"]["pick"] == 9
