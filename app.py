"""
streamlit 웹 데모 — N043 14장 지침("숫자만 보여주는 화면은 요점을 놓친다")을 반영해
절마다 누가(역할) 무엇을 읽고(읽은문서) 무엇을 썼는지(본문+인용)를 답변과 함께 보여준다.
"""
import json
from pathlib import Path

import streamlit as st

from graph import CONFIG, DOCS, run_pipeline
import metrics as metrics_mod

st.set_page_config(page_title="한류 콘텐츠 산업 딥리서치 에이전트", layout="wide")

QUESTIONS_PATH = Path(__file__).parent / "data" / "questions.json"
QUESTIONS = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))

st.title("한류 콘텐츠 산업의 확산 — 딥리서치 에이전트 데모")
st.caption(
    f"AIFFEL N043 제출용. 코퍼스 문서 {len(DOCS)}건 · 절수 {CONFIG['절수']} · "
    f"절당예산 {CONFIG['절당예산']} · 최대바퀴 {CONFIG['최대바퀴']} (gpt-4o-mini)"
)

with st.sidebar:
    st.header("질문 선택")
    labels = [f"{q['번호']}. [{q['성격']}] {q['질문']}" for q in QUESTIONS]
    choice = st.radio("9개 질문 중 하나를 고르거나 아래에 새 질문을 입력하세요.", ["(새 질문 직접 입력)"] + labels)
    custom_q = st.text_area("새 질문 입력", placeholder="예: 케이팝과 K-드라마의 해외 확산 방식은 어떻게 다른가?")
    run_btn = st.button("파이프라인 실행", type="primary")

if choice == "(새 질문 직접 입력)":
    question = custom_q.strip()
else:
    idx = labels.index(choice)
    question = QUESTIONS[idx]["질문"]
    st.sidebar.info(f"나눌만한 이유: {QUESTIONS[idx]['나눌만한이유']}")

if run_btn:
    if not question:
        st.error("질문을 선택하거나 입력하세요.")
    else:
        with st.spinner("코디네이터가 목차를 짜고, 조사관들이 자료를 조사하는 중입니다..."):
            result = run_pipeline(question)
        st.session_state["result"] = result
        st.session_state["question"] = question

if "result" in st.session_state:
    result = st.session_state["result"]
    question = st.session_state["question"]

    st.subheader("질문")
    st.write(question)

    st.subheader("절마다 누가 무엇을 읽고 썼는가")
    best = {}
    for sec in result["sections"]:
        best[sec["절"]] = sec  # 화면 표시는 최종 채택본만 (graph.py의 redelegation 정책 결과와 동일 기준)
    # 정책 적용된 최종본을 다시 계산해 정확히 맞춘다
    from graph import merge_sections
    best = merge_sections(result["sections"])
    ordered = sorted(best.values(), key=lambda s: s["번호"])

    for sec in ordered:
        with st.expander(f"{sec['번호']+1}. {sec['절']}  —  담당: {sec['역할']}", expanded=True):
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown("**읽은 문서**")
                if sec["읽은문서"]:
                    for d in sec["읽은문서"]:
                        cited_mark = "인용됨" if d in sec.get("인용", []) else "읽었지만 인용 안 함"
                        st.markdown(f"- {d} _( {cited_mark} )_")
                else:
                    st.markdown("_(읽은 문서 없음)_")
                st.markdown(f"**인용 수**: {len(sec.get('인용', []))}")
                if sec.get("부족"):
                    st.warning(f"자기신고: 부족 — {sec.get('부족사유', '')}")
            with col2:
                st.markdown("**작성한 절 본문**")
                st.write(sec["본문"])

    if result.get("redelegation_log"):
        st.subheader("재위임 원고 처리 정책 로그")
        st.caption("재위임 후 인용 수가 줄면 이전 원고를 유지하고, 늘거나 같으면 교체합니다 (REPORT.md §6 참고).")
        st.table(result["redelegation_log"])

    st.subheader("최종 보고서")
    st.markdown(result["report"])

    st.subheader("지표 (정답표 없음)")
    m = metrics_mod.compute(result)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("근거율", f"{m['근거율']*100:.1f}%")
    c2.metric("허위인용", f"{m['허위인용']}건", help="0이어야 하는 경보")
    c3.metric("편중", f"{m['편중']*100:.1f}%")
    c4.metric("읽고 안 쓴 문서", f"{m['읽고안쓴문서_수']}건")

    st.caption(
        f"컨텍스트 격리율: 코디네이터가 본 글자 수 {result.get('coordinator_chars_seen', 0):,} / "
        f"코퍼스 총 글자 수 {result.get('_corpus_total_chars', 0):,} "
        f"= {result.get('_context_isolation_rate', 0)*100:.2f}%"
    )
    if m["읽고안쓴문서_목록"]:
        st.caption("읽고 안 쓴 문서 목록: " + ", ".join(m["읽고안쓴문서_목록"]))
else:
    st.info("왼쪽에서 질문을 고르고 '파이프라인 실행'을 누르세요.")
