"""
정답표 없는 4개 지표 — N042 metrics.py를 그대로 이식(주제 무관 로직).
graph.py/baseline.py가 저장한 output/*.json (sections 리스트를 담은 result dict)을 읽어 계산한다.

[도구설계원칙 5 — 근거요구] 절 원고는 문장마다 «문서명» 인용을 요구한다.
인용 없는 문장은 근거율 계산에서 '비근거 문장'으로 잡힌다.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def load_result(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compute(result: dict) -> dict:
    # [실행 중 발견한 버그] 예전에는 여기서 "sections[절] = sec"으로 단순히 마지막 것을 덮어썼다.
    # 그러면 graph.py가 재위임 시 적용한 "인용 수가 줄면 이전 원고 유지" 정책과 무관하게, 이 지표
    # 계산기만 몰래 "무조건 최신본"으로 되돌아가 버려 실제 최종 보고서(더 나은 이전 원고를 채택)와
    # 지표가 서로 다른 걸 재는 모순이 생겼다(질문4에서 실제로 재현: 최종 보고서에는 인용 5·6개짜리
    # 절이 버젓이 들어갔는데 이 함수는 근거율 0으로 계산했었다). graph.py와 동일한 병합 함수를 써서
    # "실제로 채택된 원고"를 기준으로 지표를 계산하도록 고쳤다.
    from graph import merge_sections
    sections = merge_sections(result["sections"])

    total_sentences = 0
    grounded_sentences = 0
    all_citations = []
    all_read = set()
    fabricated = []

    for sec in sections.values():
        body = sec["본문"]
        읽은문서 = set(sec.get("읽은문서", []))
        all_read |= 읽은문서

        sentences = [s for s in re.split(r"(?<=[.다요]\.)\s+", body) if s.strip()]
        total_sentences += len(sentences)
        for s in sentences:
            if re.search(r"«[^»]+»", s):
                grounded_sentences += 1

        # .strip(): graph.py/baseline.py도 동일하게 보정하지만, 이 지표 계산기가 어떤 producer의
        # 결과를 받아도 공백 때문에 허위인용으로 오판하지 않도록 여기서도 한 번 더 방어한다.
        citations = [c.strip() for c in re.findall(r"«([^»]+)»", body)]
        all_citations.extend(citations)
        for c in citations:
            if c not in 읽은문서:
                fabricated.append((sec["절"], c))

    근거율 = grounded_sentences / total_sentences if total_sentences else 0.0
    허위인용 = len(fabricated)

    if all_citations:
        counts = Counter(all_citations)
        편중 = max(counts.values()) / sum(counts.values())
    else:
        편중 = 0.0

    cited_docs = set(all_citations)
    읽고안쓴문서 = all_read - cited_docs

    return {
        "근거율": round(근거율, 3),
        "허위인용": 허위인용,
        "허위인용_상세": fabricated,
        "편중": round(편중, 3),
        "읽고안쓴문서_수": len(읽고안쓴문서),
        "읽고안쓴문서_목록": sorted(읽고안쓴문서),
        "전체_읽은문서_수": len(all_read),
        "전체_인용_수": len(all_citations),
    }


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "output" / "run_result.json"
    result = load_result(path)
    metrics = compute(result)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
