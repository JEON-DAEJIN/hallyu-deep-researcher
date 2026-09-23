"""
한국어 위키백과에서 "한류 콘텐츠 산업의 확산" 주제의 딥리서처용 코퍼스를 모으는 스크립트.
N042/N043 부록의 "위키백과 코퍼스 수집 프롬프트" 스펙과, N042 실습에서 겪은 데이터 오염
문제(강의정리/N042/N042_실습결과보고서_2026-09-23.md 5장)의 교훈을 반영했다.

시드 문서에서 2홉까지 넓혀 문서 30건 이상을 data/corpus.json 한 파일로 저장한다.

저장 형식:
{
  "docs":  {"문서 제목": "본문 전체", ...},
  "links": {"문서 제목": ["이 문서가 가리키는 다른 문서 제목", ...], ...}
}
- links 에는 docs 안에 실제로 있는 제목만 남긴다. 밖을 가리키는 링크는 버린다.
- 본문이 3000자 미만인 토막글은 저장하지 않는다.

[도구설계원칙 2 — 실패시 대응] 429/5xx는 지수 백오프로 재시도. 그래도 실패하면
해당 문서만 건너뛰고 failed_docs에 기록한다 — 전체 수집을 중단하지 않는다.
"""
import json
import re
import time
from pathlib import Path

import requests

API = "https://ko.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "hallyu-deep-researcher/1.0 (educational use; AIFFEL N043 exercise)"}  # ASCII only
MIN_CHARS = 3000
OUT_PATH = Path(__file__).parent / "data" / "corpus.json"
MAX_DOCS = 60

# 한류 콘텐츠 산업 시드 — 음악(K-pop)/영화·드라마/산업구조(기획사·플랫폼)/위기와 대응 네 갈래를 고루 커버
SEEDS = [
    "한류", "케이팝", "방탄소년단", "블랙핑크", "아이유 (가수)", "싸이 (가수)", "강남스타일",
    "하이브 (기업)", "SM엔터테인먼트", "JYP엔터테인먼트", "YG엔터테인먼트",
    "기생충 (영화)", "봉준호", "오징어 게임", "넷플릭스", "겨울연가", "대장금",
    "한한령", "사드 배치 논란", "빌보드 차트",
]

EXCLUDE_PATTERNS = [
    re.compile(r"^\d{1,4}년"),              # 연도 문서 (예: "1876년")
    re.compile(r"^\d{1,2}월$"),             # 달 문서
    re.compile(r"^\d{1,2}월 \d{1,2}일$"),   # 날짜 문서
    re.compile(r"목록$"),                   # 목록 문서
    re.compile(r"^분류:"),
    re.compile(r"^틀:"),
]

# 거의 모든 위키 문서가 참고/외부링크로 걸어서 진짜 허브가 되는 완전 무관 문서들
# (N042 5장 "데이터 오염" 사례와 동일한 문제 — 미리 걸러둔다)
GENERIC_STOPLIST = {
    "국제 표준 도서 번호", "다음", "카카오 (기업)", "네이버", "구글", "위키백과", "위키미디어 공용",
    "유튜브", "인스타그램", "트위터", "페이스북", "나무위키",
}

# 코퍼스 목적(한류 콘텐츠 산업)과 무관하거나 너무 넓은 국가/일반 개념 허브
OUT_OF_SCOPE = {
    "대한민국", "미국", "일본", "중국", "북한", "아시아", "세계",
    "텔레비전", "영화", "음악", "대중문화", "인터넷", "자본주의",
}


def is_excluded(title: str) -> bool:
    if title in GENERIC_STOPLIST or title in OUT_OF_SCOPE:
        return True
    return any(p.search(title) for p in EXCLUDE_PATTERNS)


def api_get(params, retries=8):
    params = {**params, "format": "json", "action": "query"}
    for attempt in range(retries):
        r = requests.get(API, params=params, headers=HEADERS, timeout=20)
        if r.status_code == 429:
            wait = min(2 ** attempt, 30)
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                wait = max(wait, float(retry_after))
            print(f"    (429 rate limited, {wait}초 대기)")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return r.json()


def get_extract(title: str) -> str | None:
    """본문 전체(plain text)를 가져온다. 한 번에 한 문서만 요청 가능."""
    data = api_get({"titles": title, "prop": "extracts", "explaintext": 1, "redirects": 1})
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page:
            return None
        return page.get("extract", "") or None
    return None


def get_links(title: str) -> list[str]:
    """이 문서가 가리키는 문서 제목 목록(namespace 0만). continue 처리."""
    titles = []
    plcontinue = None
    while True:
        params = {"titles": title, "prop": "links", "plnamespace": 0, "pllimit": "max", "redirects": 1}
        if plcontinue:
            params["plcontinue"] = plcontinue
        data = api_get(params)
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            for link in page.get("links", []):
                titles.append(link["title"])
        if "continue" in data:
            plcontinue = data["continue"]["plcontinue"]
            time.sleep(0.3)
        else:
            break
    return titles


def main():
    docs: dict[str, str] = {}
    all_links: dict[str, list[str]] = {}
    visited = set()
    failed_docs: list[str] = []

    if OUT_PATH.exists():
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        docs.update(prev.get("docs", {}))
        all_links.update(prev.get("links", {}))
        visited.update(docs.keys())
        print(f"[재개] 기존 corpus.json에서 {len(docs)}건 불러옴")

    def fetch_and_store(title: str) -> bool:
        if title in visited or is_excluded(title):
            return False
        visited.add(title)
        try:
            text = get_extract(title)
            time.sleep(1.0)
            if not text or len(text) < MIN_CHARS:
                return False
            links = get_links(title)
            time.sleep(1.0)
        except requests.exceptions.HTTPError as e:
            print(f"  건너뜀(오류): {title} — {e}")
            failed_docs.append(title)
            return False
        docs[title] = text
        all_links[title] = links
        print(f"  저장: {title} ({len(text):,}자, 링크 {len(links)}개)")
        OUT_PATH.parent.mkdir(exist_ok=True)
        OUT_PATH.write_text(
            json.dumps({"docs": docs, "links": all_links}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True

    print(f"[1홉] 시드 {len(SEEDS)}건 수집")
    for seed in SEEDS:
        fetch_and_store(seed)

    print("[2홉] 시드가 공통으로 가리키는 후보 확장")
    from collections import Counter
    candidate_votes = Counter()
    for title in list(docs.keys()):
        for linked in all_links.get(title, []):
            if linked not in docs and not is_excluded(linked):
                candidate_votes[linked] += 1

    candidates = [t for t, _ in candidate_votes.most_common()]
    for title in candidates:
        if len(docs) >= MAX_DOCS:
            break
        fetch_and_store(title)

    final_links = {title: [t for t in linked if t in docs] for title, linked in all_links.items()}

    total_chars = sum(len(t) for t in docs.values())
    link_counts = sorted(len(v) for v in final_links.values())
    median_links = link_counts[len(link_counts) // 2] if link_counts else 0
    zero_link_docs = [t for t, v in final_links.items() if len(v) == 0]

    corpus = {"docs": docs, "links": final_links}
    OUT_PATH.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 수집 완료 ===")
    print(f"문서 수: {len(docs)}")
    print(f"총 글자 수: {total_chars:,}")
    print(f"문서당 링크 수 중앙값: {median_links}")
    print(f"링크 0개 문서: {zero_link_docs}")
    print(f"수집 실패 문서: {failed_docs}")
    print(f"저장 위치: {OUT_PATH}")
    print("\n[검수 필요] 위 문서 제목 목록을 사람이 눈으로 훑어 무관한 문서가 섞였는지 확인할 것 (N042 5장 교훈)")


if __name__ == "__main__":
    main()
