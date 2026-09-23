"""
N043 — "한류 콘텐츠 산업의 확산" 딥리서치 에이전트. N042의 6노드 패턴(기획→배치→조사→점검→종합→평가)을
이 프로젝트의 config.json/data/corpus.json에 맞춰 재구성했다.

핵심 설계 (PLAN.md §5, config.json 도구설계원칙 참고):
- 코디네이터에게는 문서 카드(제목+앞 250자)만 보여준다 [도구설계원칙 3]
- 배정은 코드로 검증한다 — 모델이 존재하지 않는 문서를 지어낼 수 있다 [도구설계원칙 6]
- 배치 시 "남의 구역"(다른 조사관의 시작문서)을 알려줘 중복 조사를 막는다
- 조사관은 절 원고+인용+읽은문서만 올린다 (컨텍스트 격리, 원문 전체를 올리지 않음) [도구설계원칙 3]
- 재위임은 조사관의 자기신고("부족")로만 열리고, 바퀴 수 상한으로 닫힌다 [도구설계원칙 7]
- **재위임 원고 덮어쓰기 정책(N042와 다른 점)**: 새 원고의 인용 수가 이전보다 적으면 이전 원고를 유지한다.
  늘어나거나 같으면 새 원고로 교체한다. (PLAN.md §5, REPORT.md에 근거·사례 기록)
- dispatch는 노드로 등록하지 않는다 — 오직 조건부 엣지(라우터)로만 사용한다 (N042 실습에서 겪은 버그 회피)

이 모듈은 build(ablation=..., validate=...)로 그래프를 매번 새로 조립한다.
ablation.py가 배정/역할/재위임/구역 네 스위치를 한 번에 하나씩 끄기 위해 이 팩토리 패턴을 쓴다.
validate=False 는 도구설계원칙 6 공격 시나리오 재현 전용 — 기본은 항상 True.
"""
import json
import operator
import os
import random
import re
import sys
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from dotenv import load_dotenv
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from openai import OpenAI

# Windows 콘솔 기본 코드페이지(cp949)는 «»·— 같은 문자를 못 찍어 UnicodeEncodeError를 낸다.
# 어느 스크립트에서 이 모듈을 임포트하든(baseline.py/ablation.py 등) 표준출력을 utf-8로 강제한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — 콘솔이 reconfigure를 지원하지 않아도 무시하고 계속 진행
        pass

load_dotenv()
CLIENT = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
MODEL = "gpt-4o-mini"

BASE_DIR = Path(__file__).parent
CONFIG = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
CORPUS_PATH = BASE_DIR / "data" / "corpus.json"
CARD_CHARS = 250

# 절제실험 기본값 — 전부 켜짐(기본 파이프라인). ablation.py가 하나씩 False로 바꿔 넘긴다.
ABLATION_DEFAULTS = {"배정": True, "역할": True, "재위임": True, "구역": True}


def load_corpus():
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return data["docs"], data["links"]


DOCS, LINKS = load_corpus()
TOTAL_CORPUS_CHARS = sum(len(t) for t in DOCS.values())


def call_llm(system: str, user: str, json_mode: bool = False, retries: int = 3) -> str:
    """[도구설계원칙 2] OpenAI 호출 실패시 짧게 재시도하고, 그래도 실패하면 빈 문자열로 넘어간다
    (호출 하나 실패했다고 전체 파이프라인을 죽이지 않는다)."""
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    last_err = None
    for attempt in range(retries):
        try:
            resp = CLIENT.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.3,
                **kwargs,
            )
            return resp.choices[0].message.content
        except Exception as e:  # noqa: BLE001 — 외부 API 호출, 광범위 예외를 의도적으로 잡는다
            last_err = e
            print(f"    (LLM 호출 실패, 재시도 {attempt+1}/{retries}: {e})")
    print(f"    (LLM 호출 최종 실패, 건너뜀: {last_err})")
    return ""


def extract_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------- State ----------------
class State(TypedDict):
    question: str
    toc: list
    sections: Annotated[list, operator.add]
    visited: Annotated[list, operator.add]
    wheel: int
    report: str
    coordinator_chars_seen: Annotated[int, operator.add]
    redelegation_log: list  # review()가 매번 전체를 다시 계산해 덮어쓴다 (중복 로그 방지)


def cards() -> str:
    """문서 카드: 제목 + 앞 250자. 코디네이터에게는 이것만 보여준다. [도구설계원칙 3]"""
    lines = []
    for title, text in DOCS.items():
        lines.append(f"- {title}: {text[:CARD_CHARS].strip()}...")
    return "\n".join(lines)


def merge_sections(sections_list: list, log: Optional[list] = None) -> dict:
    """절제(§) 별 '최종 채택 원고'를 고른다.
    재위임으로 같은 절이 여러 번 등장하면, 무조건 최신본을 쓰지 않고
    인용 수가 줄어들면 이전 원고를 유지한다 (PLAN.md §5 정책)."""
    best: dict = {}
    for sec in sections_list:
        key = sec["절"]
        if key not in best:
            best[key] = sec
            continue
        prev = best[key]
        new = sec
        prev_n = len(prev.get("인용", []))
        new_n = len(new.get("인용", []))
        if new_n < prev_n:
            decision = "이전_원고_유지"
            kept = prev
        else:
            decision = "신규_원고로_교체"
            kept = new
        if log is not None:
            log.append({
                "절": key,
                "결정": decision,
                "이전_인용수": prev_n,
                "신규_인용수": new_n,
            })
        best[key] = kept
    return best


def make_builders(ablation: dict, validate: bool):
    """ablation/validate 설정을 클로저로 감싼 노드 함수 5개(plan/dispatch/researcher/review/synthesize)를 만든다.
    build()가 매 설정마다 이 함수를 호출해 완전히 새 그래프를 조립한다 — 절제실험은 상태 필드가 아니라
    그래프 자체를 다시 만드는 방식으로 '한 번에 하나씩만' 끈다."""

    max_wheel = CONFIG["최대바퀴"] if ablation["재위임"] else 0

    # ---------------- ①기획 plan ----------------
    def plan(state: State) -> dict:
        card_text = cards()
        if ablation["역할"]:
            role_line = (
                f"절마다 아래 역할명단 중 하나를 순서대로 배정하라: {', '.join(CONFIG['역할명단'])}."
            )
        else:
            role_line = "역할 구분 없이 모든 절에 동일하게 '조사 담당'이라는 역할명을 써라 (역할 끄기 실험)."

        prompt = f"""질문: {state['question']}

아래는 코퍼스에 있는 문서 카드(제목+앞 250자) 목록이다.
{card_text}

이 질문에 답하는 보고서를 쓰기 위한 목차를 {CONFIG['절수']}개 절로 나눠라.
목차는 반드시 내용 단위로 나눠라 (예: 음악 확산/영화·드라마/산업구조/위기와 대응).
형식 단위(개요/연표/분석/결론)로 나누면 조사관들이 같은 문서를 중복해서 읽는다.
{role_line}

각 절마다 절 제목, 역할(한 줄), 시작문서(위 문서 카드 제목 중 하나), 예산({CONFIG['절당예산']})을 정하라.

JSON으로만 답하라:
{{"toc": [{{"절": "...", "역할": "...", "시작문서": "...", "예산": {CONFIG['절당예산']}}}, ...]}}"""
        raw = call_llm(
            "너는 리서치 팀의 코디네이터다. 문서 카드만 보고 목차와 배정을 정한다.",
            prompt,
            json_mode=True,
        )
        parsed = extract_json(raw)
        raw_toc = parsed.get("toc", [])

        taken = set()
        toc = []
        doc_titles = list(DOCS.keys())
        for i, item in enumerate(raw_toc[: CONFIG["절수"]]):
            seed = item.get("시작문서", "")
            if not ablation["배정"]:
                # [배정 끄기 ablation] 코디네이터의 배정을 무시하고 랜덤 시작문서를 쓴다.
                choices = [t for t in doc_titles if t not in taken] or doc_titles
                seed = random.choice(choices)
            elif validate:
                # [도구설계원칙 6] 배정 검증: 모델이 지어낸 문서명이면 실제 존재하는 문서로 교체한다.
                if seed not in DOCS or seed in taken:
                    seed = next((t for t in doc_titles if t not in taken), doc_titles[0])
            else:
                # [검증 끄기 — 공격 시나리오 재현 전용] 지어낸 문서명이라도 그대로 통과시킨다.
                pass
            taken.add(seed)
            role = CONFIG["역할명단"][i % len(CONFIG["역할명단"])] if ablation["역할"] else "조사 담당"
            toc.append({
                "절": item.get("절", f"절{i+1}"),
                "역할": item.get("역할", role) if ablation["역할"] else "조사 담당",
                "시작문서": seed,
                "예산": CONFIG["절당예산"],
                "번호": i,
                "피하기": [],
                "지시": "",
            })
        print(f"[기획] 목차 {len(toc)}개 절 확정: {[t['절'] for t in toc]}")
        print(f"       시작문서 배정: {[(t['절'], t['시작문서']) for t in toc]}")
        return {"toc": toc, "wheel": 0, "coordinator_chars_seen": len(card_text), "redelegation_log": []}

    # ---------------- ②배치 dispatch (노드가 아니라 라우터 함수) ----------------
    def dispatch(state: State):
        """plan/review 다음에 오는 조건부 엣지 함수. 절대 g.add_node로 등록하지 않는다
        (N042 실습에서 노드+라우터 이중 등록으로 InvalidUpdateError가 났던 버그를 처음부터 회피)."""
        사절 = state["toc"]
        tasks = []
        for i, t in enumerate(사절):
            if ablation["구역"]:
                남의구역 = {other["시작문서"] for j, other in enumerate(사절) if j != i}
            else:
                남의구역 = set()  # [구역 끄기 ablation] 피하기 리스트를 비운다
            task = {**t, "피하기": list(남의구역)}
            tasks.append(Send("researcher", {"question": state["question"], "task": task}))
        return tasks

    # ---------------- ③조사 researcher ----------------
    def read_one(title: str, question: str, section: str) -> str:
        text = DOCS.get(title, "")
        if not text:
            return "관련 없음"
        prompt = f"""질문: {question}
절 주제: {section}

아래 문서를 읽고 이 절 주제와 관련된 내용을 여섯 문장 이내로 요약하라.
관련이 없으면 "관련 없음"이라고만 답하라.

문서 «{title}»:
{text[:6000]}"""
        return call_llm("너는 문서를 읽고 핵심만 요약하는 조사관이다.", prompt)

    def researcher(payload: dict) -> dict:
        question = payload["question"]
        task = payload["task"]
        budget = task["예산"]
        avoid = set(task.get("피하기", []))
        start = task["시작문서"]

        visited_docs = []
        notes = []
        frontier = [start] + [d for d in LINKS.get(start, []) if d not in avoid]
        seen_frontier = set()

        while len(visited_docs) < budget and frontier:
            title = frontier.pop(0)
            if title in visited_docs or title in seen_frontier:
                continue
            seen_frontier.add(title)
            if title not in DOCS:
                # [도구설계원칙 6] 배정 검증을 꺼둔 상태에서 모델이 지어낸 문서명이 여기로 들어올 수 있다.
                # 존재하지 않으므로 그냥 건너뛴다 — 이 자체가 검증 없는 경우의 실패 재현이다.
                continue
            summary = read_one(title, question, task["절"])
            visited_docs.append(title)
            if "관련 없음" not in summary:
                notes.append((title, summary))
            for linked in LINKS.get(title, []):
                if linked not in avoid and linked not in seen_frontier:
                    frontier.append(linked)

        if not notes:
            return {"sections": [{
                "절": task["절"], "역할": task["역할"], "번호": task["번호"],
                "본문": "관련 자료를 찾지 못했습니다.", "인용": [], "읽은문서": visited_docs,
                "부족": True, "부족사유": "관련 문서를 찾지 못함",
            }]}

        evidence = "\n\n".join(f"«{t}»: {s}" for t, s in notes)
        write_prompt = f"""질문: {question}
절 제목: {task['절']} (역할: {task['역할']})
{('지시: ' + task['지시']) if task.get('지시') else ''}

아래는 이 절을 위해 조사한 근거들이다:
{evidence}

이 근거들만 사용해서 절 원고를 작성하라. 문장마다 근거가 된 문서를 «문서명» 형식으로 인용하라.
근거가 부족하면 마지막 줄에 "[부족: 이유]"를 덧붙여라."""
        body = call_llm("너는 근거 문서만 인용하며 절 원고를 쓰는 조사관이다.", write_prompt)

        부족 = "[부족" in body
        부족사유 = ""
        if 부족:
            m = re.search(r"\[부족[:：]\s*(.*?)\]", body)
            부족사유 = m.group(1) if m else "근거 부족"
            body = re.sub(r"\[부족.*?\]", "", body).strip()

        # .strip() 필수: LLM이 이따금 «H.O.T. »처럼 여는/닫는 기호 안쪽에 공백을 남겨,
        # 실제로는 올바른 인용인데도 읽은문서 목록과 문자열이 안 맞아 허위인용으로 오판되는 것을 막는다.
        인용 = [c.strip() for c in re.findall(r"«([^»]+)»", body)]

        return {"sections": [{
            "절": task["절"], "역할": task["역할"], "번호": task["번호"],
            "본문": body, "인용": 인용, "읽은문서": visited_docs,
            "부족": 부족, "부족사유": 부족사유,
        }]}

    # ---------------- ④점검 review ----------------
    def review(state: State):
        log: list = []
        best = merge_sections(state["sections"], log)
        wheel = state["wheel"]
        부족목록 = [s for s in best.values() if s.get("부족")]
        extra_chars_seen = sum(len(s.get("부족사유", "")) for s in 부족목록)

        if 부족목록 and wheel < max_wheel:
            newtoc = []
            for s in 부족목록:
                newtoc.append({
                    "절": s["절"], "역할": s["역할"], "번호": s["번호"],
                    "시작문서": s["읽은문서"][-1] if s["읽은문서"] else list(DOCS.keys())[0],
                    "예산": CONFIG["절당예산"], "피하기": [],
                    "지시": f"지난번에 {', '.join(s['읽은문서']) or '자료 없음'}을 읽었지만 {s['부족사유']}. 새로운 자료를 찾아라.",
                })
            print(f"[점검] {len(newtoc)}개 절 재위임 (바퀴 {wheel+1})")
            return {"toc": newtoc, "wheel": wheel + 1, "coordinator_chars_seen": extra_chars_seen,
                    "redelegation_log": log}

        print(f"[점검] 통과 — 부족 {len(부족목록)}건, 그대로 종합으로 진행 (최대바퀴={max_wheel})")
        return {"toc": [], "coordinator_chars_seen": extra_chars_seen, "redelegation_log": log}

    def route_after_review(state: State):
        if state["toc"]:
            return dispatch(state)
        return "synthesize"

    # ---------------- ⑤종합 synthesize ----------------
    def synthesize(state: State) -> dict:
        best = merge_sections(state["sections"])
        ordered = sorted(best.values(), key=lambda s: s["번호"])

        outline = "\n".join(f"{i+1}. {x['절']}: {x['본문'][:90]}…" for i, x in enumerate(ordered))
        prompt = f"""질문: {state['question']}

아래는 각 절의 제목과 본문 시작 90자다:
{outline}

이 개요만 보고 보고서 전체의 머리말과 맺음말만 작성하라(각 100~150자).
JSON으로만 답하라: {{"머리말": "...", "맺음말": "..."}}"""
        raw = call_llm("너는 절 원고를 종합하는 편집자다. 절 본문은 다시 쓰지 않는다.", prompt, json_mode=True)
        parsed = extract_json(raw)

        parts = [parsed.get("머리말", "")]
        for i, x in enumerate(ordered):
            parts.append(f"\n## {i+1}. {x['절']} ({x['역할']})\n{x['본문']}")
        parts.append(f"\n{parsed.get('맺음말', '')}")
        report = "\n".join(parts)
        print(f"[종합] 보고서 {len(report)}자 완성")
        return {"report": report, "coordinator_chars_seen": len(outline)}

    return plan, dispatch, researcher, review, synthesize, route_after_review


def build(ablation: Optional[dict] = None, validate: bool = True):
    ablation = {**ABLATION_DEFAULTS, **(ablation or {})}
    plan, dispatch, researcher, review, synthesize, route_after_review = make_builders(ablation, validate)

    g = StateGraph(State)
    for name, fn in [("plan", plan), ("researcher", researcher), ("review", review), ("synthesize", synthesize)]:
        g.add_node(name, fn)
    g.set_entry_point("plan")
    g.add_conditional_edges("plan", dispatch, ["researcher"])
    g.add_edge("researcher", "review")
    g.add_conditional_edges("review", route_after_review, ["researcher", "synthesize"])
    g.add_edge("synthesize", END)
    return g.compile()


def run_pipeline(question: str, ablation: Optional[dict] = None, validate: bool = True) -> dict:
    """한 질문에 대해 파이프라인을 한 번 실행하고 result dict(sections/report/...) + 부가 정보를 반환한다."""
    app = build(ablation=ablation, validate=validate)
    result = app.invoke(
        {"question": question, "sections": [], "visited": [], "coordinator_chars_seen": 0, "redelegation_log": []},
        {"recursion_limit": 50},
    )
    result["_corpus_total_chars"] = TOTAL_CORPUS_CHARS
    result["_context_isolation_rate"] = (
        round(result.get("coordinator_chars_seen", 0) / TOTAL_CORPUS_CHARS, 4) if TOTAL_CORPUS_CHARS else 0.0
    )
    return result


if __name__ == "__main__":
    question = "케이팝이 해외 시장에서 확산된 배경은 무엇이고, 이를 가능케 한 산업적 요인(기획사 시스템·플랫폼·팬덤)은 무엇인가?"
    result = run_pipeline(question)
    print("\n===== 최종 보고서 =====\n")
    print(result["report"])
    print(f"\n[컨텍스트 격리] 코디네이터가 본 글자 수: {result['coordinator_chars_seen']:,} / "
          f"코퍼스 총 글자 수: {result['_corpus_total_chars']:,} "
          f"(격리율 {result['_context_isolation_rate']*100:.2f}%)")
    if result["redelegation_log"]:
        print(f"\n[재위임 정책 로그]")
        for entry in result["redelegation_log"]:
            print(f"  {entry}")

    out_path = Path(__file__).parent / "output" / "run_result.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")
