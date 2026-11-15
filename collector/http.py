"""표준 라이브러리 HTTP 도우미 — 타임아웃, 지수 백오프 재시도."""
from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.request

# 이름에 "collector"가 들어가면 한국섬유신문 RSS가 403 페이지를 준다(2026-11-13 확인, 사용자 결정으로 이름만 바꿈).
# 브라우저로 위장하지 않고 우리 이름·연락 주소는 그대로 밝힌다.
UA = "fashion-trend-news/1.0 (+https://github.com/js87ahnx-droid)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(Exception):
    pass


def get(url: str, *, headers: dict | None = None, timeout: float = 15, retries: int = 3, sleep=time.sleep) -> bytes:
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    delay = 1.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=timeout) as r:
                return ungzip(r.read())
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == retries - 1:
                raise FetchError(f"HTTP {e.code} {url}") from e
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == retries - 1:
                raise FetchError(f"{type(e).__name__} {url}: {e}") from e
            wait = delay
        sleep(min(wait, 30))
        delay *= 2
    raise FetchError(f"재시도 소진 {url}")


GZIP_MAGIC = bytes([0x1F, 0x8B])


def ungzip(body: bytes) -> bytes:
    """gzip 머리(1f 8b)로 시작하면 푼다. 아이즈매거진 뉴스 사이트맵(.xml.gz)은 파일 자체가 gzip인데 서버가
    Accept-Encoding: identity에도 Content-Encoding: gzip을 붙여 두 겹일 수 있어 최대 2번 푼다(2026-11-15 확인)."""
    for _ in range(2):
        if body[:2] != GZIP_MAGIC:
            break
        body = gzip.decompress(body)
    return body


def get_text(url: str, **kw) -> str:
    return get(url, **kw).decode("utf-8", errors="replace")
