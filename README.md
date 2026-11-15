# 패션 동향 수집함

국내외 패션 업계 소식(BoF·Vogue Business·WWD·국내 패션 산업지 등)을 3시간마다 모아
Claude가 한국어로 정리해 주제별로 보여 주는 정적 사이트. [AI 동향 수집함](https://github.com/js87ahnx-droid/ai-trend-collector)의 코드를 복사해 패션용으로 고쳤다(따로 운영).

- 화면: GitHub Pages (`web/index.html` + `data/items.json`·`data/status.json`)
- 수집: GitHub Actions `17 */3 * * *` → `python -m collector.main` → `data/` 커밋 → Pages 재배포

## 설정

저장소 **Settings → Secrets and variables → Actions** 에 등록:

| 이름 | 용도 | 없으면 |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Max 구독 토큰(`claude setup-token`으로 만듦). 워크플로가 Claude Code를 설치해 `claude -p`로 분류(Opus 5.5)·기사 정리(Sonnet 5.5) — `collector/cc_client.py` | 수집만 하고 저장 안 함 (`last_run`만 갱신) |

로컬이나 다른 곳에서 `ANTHROPIC_API_KEY`가 있으면 그쪽(Anthropic SDK)을 먼저 쓴다(`classify.make_client`). 워크플로는 API 키를 넘기지 않는다.
구독 토큰을 자동 실행에 쓰는 것은 구독 약관상 회색 지대다(자동화 접근은 API 키를 권함) — 개인 용도로만 쓴다.

모델 교체: 워크플로 `env`에 `ANTHROPIC_MODEL`(분류) 또는 `ANTHROPIC_DETAIL_MODEL`(기사 정리)을 추가.

## 로컬 실행

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
.venv/Scripts/python -m pytest -q
.venv/Scripts/python scripts/live_check.py        # 매체별 실제 수집 건수만 확인 (저장·LLM 없음)
.venv/Scripts/python -m collector.main            # 전체 1회차 (DATA_DIR로 출력 위치 변경 가능)
.venv/Scripts/python scripts/build_site.py && .venv/Scripts/python -m http.server -d _site
```

아이콘(로컬에서 한 번, Pillow·Georgia Bold·Edge 필요): `scripts/make_icons.py`로 `web/icon-*.png`를 만들고
`scripts/check_icon.py`로 `web/icon.svg`와 같은지 확인한다.

## 수집 출처

| `source` | 매체 | 방법 | 원문 |
|---|---|---|---|
| `feed` | WWD · Glossy · Fashionista | RSS (`collector/sources/feeds.py` `FEEDS`), 48시간, 매체당 5건 | 읽음 |
| `feed` | Vogue Business | Vogue RSS 중 `Business` 분류 글만 | 안 읽음(brief) — vogue.com robots.txt가 Claude 수집기를 막음 |
| `feed` | Hypebeast · 바자코리아 | RSS + 스타일 필터 | 읽음 |
| `feed` | 보그코리아 · W코리아 | RSS + 스타일 필터 | 안 읽음(brief) — robots.txt가 ClaudeBot을 막음 |
| `feed` | 한국섬유신문 | RSS, 매체당 8건, http 링크는 https로 | 읽음 |
| `krsite` | 어패럴뉴스 · 패션엔 · 패션비즈 | 첫 화면 링크(패션비즈는 뉴스 사이트맵) → 기사 메타만 (`krsite.py` `SITES`), 매체당 6건, robots.txt 확인 | 읽음 |
| `krsite` | 아이즈매거진 | 뉴스 사이트맵 `sitemap-news.xml.gz`(gzip)의 주소·제목·게시 시각 → **사이트맵 제목으로** 스타일 필터 → 통과한 글만 기사 페이지의 `og:description`, 6건 | 읽음 |
| `feed` | Pambianco (IT) | RSS, 매체당 5건 | 읽음 |
| `feed` | FashionUnited (IT) | RSS 중 `news/business`·`news/retail`·`news/moda` 분류만(보도자료·문화 제외) | 읽음 |
| `feed` | Il Sole 24 Ore (IT) | RSS(모다), 3건 | 안 읽음(brief) — robots.txt가 anthropic-ai를 막음 |
| `gnews` | The Business of Fashion | 구글 뉴스 RSS `site:businessoffashion.com when:2d` (`gnews.py` `QUERIES`), 6건 | 안 읽음(brief) — 링크는 구글 뉴스 주소 그대로 |
| `gnews` | FashionNetwork (FR) · FashionNetwork (IT) | 구글 뉴스(프랑스어·이탈리아어판) `site:fr.fashionnetwork.com` / `site:it.fashionnetwork.com`, 각 4건 | 안 읽음(brief) — RSS 403, robots.txt가 Claude 수집기 일부를 막음 |
| `gnews` | Les Echos (FR) | 구글 뉴스(프랑스어판) `site:lesechos.fr luxe`, 3건 | 안 읽음(brief) — RSS 403, 유료 |

**스타일 필터**(`feeds.style_keep`, 스타일 잡지 5곳 — Hypebeast·보그코리아·W코리아·바자코리아·아이즈매거진): (가) 비즈니스 단어(`BIZ_WORDS`: 매출·실적·인수·CEO·매장·리테일…)가 있으면 남김 → (나) 없으면 업계 단어(`INDUSTRY_WORDS`: 협업·콜라보·선임·사임·팝업·진출…, collaboration·launch·appoint·steps down… / 제목의 `A x B` 협업 표기)가 있고 홍보 단어(`PROMO_WORDS`: 세일·할인·발매·래플·응모·추천·최대 N%, sale·restock·release date·colorway·$가격)가 없을 때만 남김 → (다) 그 밖엔 버림. 단어는 제목 + 요약 앞 300자에서 찾고(아이즈매거진은 제목만), 협업 표기는 제목에서만 찾는다. 수집 상태에는 "필터 전 N건 중 M건"으로 보인다.

프랑스·이탈리아 매체는 `region="global"`(화면 표시 "해외")이고, 매체 이름에 (FR)·(IT)를 붙여 구분한다. 분류·상세 프롬프트는 외국어 원문이어도 한국어로 쓰게 한다.

brief 글은 원문을 열지 않고 제목·매체·RSS 요약만으로 한 줄 소개를 쓰며, 화면에 "원문 미확인 · 제목만 소개"로 표시되고 오늘의 픽에 오르지 않는다.
같은 소식을 원문을 읽은 글이 나중에 다루면 그 글이 brief 글을 대체한다.

User-Agent는 `fashion-trend-news/1.0 (+https://github.com/js87ahnx-droid)` — 이름에 "collector"가 들어가면 한국섬유신문이 막는다. 브라우저로 위장하지 않는다.

## 운영 체크리스트

| 항목 | 위치 |
|---|---|
| 출처별·매체별 실패 격리 | `collector/main.py` 수집 루프, 각 출처 `fetch`의 매체 루프 |
| 매체별 건수·실패 사유 | `data/status.json`의 `sources.*.outlets`, 화면 하단 "수집 상태" |
| LLM 호출 | 분류 회차당 1회(Opus), 상세 글마다 1회(Sonnet, web fetch 1회·4천 토큰) |
| 비용 상한 | 회차당 상세 5건(그중 brief 2자리, `main.DETAIL_PER_RUN`·`BRIEF_SLOTS`), 최근 24시간 40회(`DETAIL_PER_DAY`, 실패 포함) |
| 사용량 기록 | `status.json`의 `run.usage`(모델별 토큰) · `usage_24h`(최근 24시간) |
| 계속 실패하는 글 | 상세 예외 2번이면 건너뜀(`data/failures.json` → `skipped.json`) |
| 보관 | 7일(`store.RETENTION`), 대체된 글은 3일 |
| 원문 복사 금지 | 상세 프롬프트에 직접 요약·인용 금지, 저장 필드에 본문 없음 |
| 요청 한도 | `collector/http.py` 지수 백오프(429·5xx, `Retry-After` 존중) |
| 6시간 멈추면 "업데이트 늦어짐" | `web/index.html` `LATE_MS` |

공개 저장소의 예약 워크플로는 60일간 저장소 활동이 없으면 GitHub가 멈춘다. 데이터 커밋이 활동으로 잡히지만, 멈추면 Actions 탭에서 다시 켠다.
