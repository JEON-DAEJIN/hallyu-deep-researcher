"""
공정한 단일 에이전트 대조군 (PLAN.md §7 체크리스트를 그대로 구현).

공정성 체크리스트:
- [x] 팀과 동일한 코퍼스(data/corpus.json) 사용 — graph.py의 DOCS/LINKS를 그대로 재사용
- [x] 팀과 동일한 모델(gpt-4o-mini) 사용 — graph.py의 call_llm 재사용
- [x] 팀의 절수×절당예산과 같은 총 읽기 예산(4×4=16건)을 혼자서도 다 씀
- [x] 예산을 실제로 다 쓰는지 로그로 확인 (BFS 프론티어가 바닥나면 그 사실을 명시적으로 출력)

단일 에이전트가 "한류" 루트 문서에서 출발해 BFS로 링크를 따라가며 예산만큼 문서를 읽고,
전체 근거를 모아 한 편의 장문 보고서를 쓴다(문장마다 «문서명» 인용 요구 — graph.py와 동일 규칙).
"""
import json
from pathlib import Path

from graph import CONFIG, DOCS, LINKS, TOTAL_CORPUS_CHARS, build_excerpt, call_llm, extract_json  # noqa: F401
import re

BUDGET = CONFIG["절수"] * CONFIG["절당예산"]  # 4*4 = 16, 팀 전체 예산과 동일
ROOT_SEED = "한류"  # 팀 코디네이터가 카드에서 고를 법한 가장 포괄적인 시작문서와 동급


def read_one(title: str, question: str) -> str:
    text = DOCS.get(title, "")
    # graph.py의 read_one과 동일한 완화된 기준을 쓴다 (공정성 — 팀만 관대한 기준을 쓰면 안 됨).
    prompt = f"""질문: {question}

아래 문서에서 이 질문을 다루는 보고서에 배경·근거로 쓸 수 있는 사실(역사·조직·정책·사건·구조 등)을
최대한 찾아 여섯 문장 이내로 요약하라. 질문과 완벽히 같은 표현이 아니어도, 답변을 뒷받침하는 데
조금이라도 도움이 되면 요약하라. 문서에 없는 내용은 지어내지 마라.
이 문서가 질문의 전체 주제와 아예 무관할 때만 "관련 없음"이라고 답하라.

문서 «{title}»:
{build_excerpt(text, question, "")}"""
    return call_llm("너는 문서를 읽고 핵심만 요약하는 리서처다.", prompt)


def run_baseline(question: str, budget: int = BUDGET, root: str = ROOT_SEED) -> dict:
    start = root if root in DOCS else next(iter(DOCS.keys()))
    visited_docs: list[str] = []
    notes: list[tuple[str, str]] = []
    frontier = [start] + list(LINKS.get(start, []))
    seen_frontier = {start}

    while len(visited_docs) < budget and frontier:
        title = frontier.pop(0)
        if title in visited_docs or title not in DOCS:
            continue
        summary = read_one(title, question)
        visited_docs.append(title)
        if "관련 없음" not in summary:
            notes.append((title, summary))
        for linked in LINKS.get(title, []):
            if linked not in seen_frontier:
                seen_frontier.add(linked)
                frontier.append(linked)

    budget_exhausted_by_reading = len(visited_docs) >= budget
    frontier_ran_dry = not frontier and len(visited_docs) < budget
    print(f"[대조군] 읽은 문서 {len(visited_docs)}/{budget}건"
          + (" — 예산을 전부 소진함" if budget_exhausted_by_reading else
             " — 프론티어가 바닥나 예산을 다 쓰지 못함 (공정성 주의: BFS 그래프 범위 한계)" if frontier_ran_dry else ""))

    evidence = "\n\n".join(f"«{t}»: {s}" for t, s in notes) or "(관련 근거를 찾지 못함)"
    write_prompt = f"""질문: {question}

아래는 이 질문에 답하기 위해 혼자 조사한 근거들이다({len(notes)}건, 총 {len(visited_docs)}개 문서를 읽음):
{evidence}

이 근거들만 사용해서 장문의 보고서를 작성하라(여러 문단, 소제목 가능). 문장마다 근거가 된 문서를
«문서명» 형식으로 인용하라. 근거가 부족한 부분이 있으면 명시하라."""
    report_body = call_llm("너는 혼자서 리서치부터 보고서 작성까지 다 하는 단일 에이전트다.", write_prompt)

    인용 = [c.strip() for c in re.findall(r"«([^»]+)»", report_body)]  # graph.py와 동일한 공백 보정

    # metrics.py와 호환되도록 "sections" 리스트 하나짜리로 감싼다 (단일 에이전트=단일 절).
    result = {
        "question": question,
        "sections": [{
            "절": "단일 에이전트 보고서", "역할": "단일 에이전트", "번호": 0,
            "본문": report_body, "인용": 인용, "읽은문서": visited_docs,
            "부족": False, "부족사유": "",
        }],
        "report": report_body,
        "budget": budget,
        "budget_used": len(visited_docs),
        "budget_exhausted": budget_exhausted_by_reading,
        "frontier_ran_dry": frontier_ran_dry,
        "coordinator_chars_seen": 0,  # 단일 에이전트는 코디네이터가 없다 — 격리 개념 자체가 없음
        "_corpus_total_chars": TOTAL_CORPUS_CHARS,
    }
    return result


if __name__ == "__main__":
    question = "케이팝이 해외 시장에서 확산된 배경은 무엇이고, 이를 가능케 한 산업적 요인(기획사 시스템·플랫폼·팬덤)은 무엇인가?"
    print(f"[대조군] 예산 {BUDGET}건(팀과 동일: 절수{CONFIG['절수']}×절당예산{CONFIG['절당예산']}), 시작문서: {ROOT_SEED}")
    result = run_baseline(question)
    print("\n===== 대조군 보고서 =====\n")
    print(result["report"])

    out_path = Path(__file__).parent / "output" / "baseline_result.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")
