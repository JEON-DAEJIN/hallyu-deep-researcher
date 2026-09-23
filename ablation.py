"""
절제실험(ablation) — 배정→역할→재위임→구역, 한 번에 하나씩만 끈다 (PLAN.md §8, N042 13장).

대표 질문 2건(복합 1 + 추적 1) × 5개 설정 × 3회 반복 = 30회 실행.
같은 설정도 시행마다 근거율이 십몇%p씩 흔들릴 수 있다는 N042 12장의 교훈에 따라,
평균만이 아니라 **매 회차의 원본 결과를 전부** output/ablation.json에 남긴다.

[도구설계원칙 4 — 비싼행동엔 확인] 실행 전 예상 LLM 호출 수를 콘솔에 출력한다.
"""
import json
from pathlib import Path

from graph import run_pipeline, ABLATION_DEFAULTS, CONFIG
import metrics as metrics_mod

OUT_PATH = Path(__file__).parent / "output" / "ablation.json"
QUESTIONS_PATH = Path(__file__).parent / "data" / "questions.json"
REPEATS = 3

CONFIGS = [
    ("기본(전부 켜짐)", {}),
    ("배정_끄기(랜덤 시작문서)", {"배정": False}),
    ("역할_끄기(전 절 동일 역할)", {"역할": False}),
    ("재위임_끄기(최대바퀴=0)", {"재위임": False}),
    ("구역_끄기(피하기 리스트 비움)", {"구역": False}),
]


def pick_representative_questions() -> list[dict]:
    all_q = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    복합 = next(q for q in all_q if q["성격"] == "복합")
    추적 = next(q for q in all_q if q["성격"] == "추적")
    return [복합, 추적]


def estimate_calls(n_questions: int) -> int:
    절수 = CONFIG["절수"]
    예산 = CONFIG["절당예산"]
    최대바퀴 = CONFIG["최대바퀴"]
    # 절당 최악의 경우: (예산개 read_one + 1 write) × (최대바퀴+1 회차), + plan 1회 + synthesize 1회
    per_run_worst = 절수 * (예산 + 1) * (최대바퀴 + 1) + 2
    return per_run_worst * len(CONFIGS) * n_questions * REPEATS


def main():
    questions = pick_representative_questions()
    est = estimate_calls(len(questions))
    print(f"[ablation] 설정 {len(CONFIGS)}개 × 질문 {len(questions)}개 × 반복 {REPEATS}회 = "
          f"{len(CONFIGS) * len(questions) * REPEATS}회 실행")
    print(f"[ablation] 최악의 경우 예상 LLM 호출 수(대략): {est}회 — 진행합니다 (gpt-4o-mini, 도구설계원칙 4)")

    runs = []
    for q in questions:
        for config_name, overrides in CONFIGS:
            for rep in range(1, REPEATS + 1):
                print(f"\n--- [{config_name}] 질문{q['번호']}({q['성격']}) 반복 {rep}/{REPEATS} ---")
                result = run_pipeline(q["질문"], ablation=overrides)
                m = metrics_mod.compute(result)
                run_record = {
                    "config": config_name,
                    "overrides": overrides,
                    "질문_번호": q["번호"],
                    "질문_성격": q["성격"],
                    "질문": q["질문"],
                    "반복": rep,
                    "metrics": m,
                    "wheel_도달": result.get("wheel", 0),
                    "redelegation_log": result.get("redelegation_log", []),
                    "context_isolation_rate": result.get("_context_isolation_rate", 0.0),
                }
                runs.append(run_record)
                print(f"  근거율={m['근거율']} 허위인용={m['허위인용']} 편중={m['편중']} "
                      f"읽고안쓴문서={m['읽고안쓴문서_수']}")
                # 매 회차마다 즉시 저장 — 중간에 실패해도 지금까지의 결과는 보존 [도구설계원칙 2]
                OUT_PATH.parent.mkdir(exist_ok=True)
                OUT_PATH.write_text(json.dumps({"runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[ablation] 전체 {len(runs)}회 실행 완료. 저장 위치: {OUT_PATH}")

    # 설정별 요약(평균) — 원본 값은 이미 위에서 다 저장했으니 참고용으로만 계산
    from collections import defaultdict
    by_config = defaultdict(list)
    for r in runs:
        by_config[(r["config"], r["질문_번호"])].append(r["metrics"]["근거율"])
    print("\n[ablation] 설정×질문별 근거율 (평균 / 최솟값-최댓값 변동폭):")
    for (config_name, qnum), vals in by_config.items():
        avg = sum(vals) / len(vals)
        spread = max(vals) - min(vals)
        print(f"  {config_name} / 질문{qnum}: 평균 {avg:.3f}, 변동폭 {spread:.3f} (원값 {vals})")


if __name__ == "__main__":
    main()
