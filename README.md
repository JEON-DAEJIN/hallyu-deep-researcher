# hallyu-deep-researcher

AIFFEL N043 과제 — "나만의 딥리서치 에이전트 만들기". N042에서 배운 코디네이터+조사관+편집자 6노드 LangGraph 리서치 파이프라인을, **"한류 콘텐츠 산업의 확산"**이라는 나만의 주제로 처음부터 다시 설계해서 제출한다.

> 진행 상황과 설계 근거는 [PLAN.md](PLAN.md)를, 최종 제출 보고서는 [REPORT.md](REPORT.md)를 참고.

## 알려진 한계 (자기 점검)

- **Ablation은 지표 비교 위주다**: 배정/역할/재위임/구역을 하나씩 끈 30회 실행은 근거율·편중 등
  숫자로만 비교했다. N043 12장이 요구하는 "설정별 보고서를 나란히 직접 읽고 비교"는 공정한 대조군
  비교(팀 vs baseline, 질문4)에서만 했고, ablation 각 설정의 보고서 원문 비교는 생략했다 —
  `ablation.py`가 지표만 `output/ablation.json`에 남기고 보고서 텍스트는 저장하지 않기 때문이다.
  자세한 내용과 보완 방향은 [REPORT.md의 "Ablation 결과"](REPORT.md#ablation-결과--변동폭을-먼저-보고-단정하지-않는다) 절 참고.

## 실행 방법

```bash
pip install -r requirements.txt
cp .env.example .env   # OPENAI_API_KEY 채우기

python collect_corpus.py   # 1) 위키백과에서 코퍼스 수집
python graph.py             # 2) 질문 하나로 파이프라인 실행
python baseline.py          # 3) 단일 에이전트 대조군
python ablation.py          # 4) 절제실험
streamlit run app.py        # 5) 웹 데모
```

## 폴더 구조

```
hallyu-deep-researcher/
├── data/            # corpus.json (docs+links) · questions.json (질문 9건)
├── config.json      # 절수·절당예산·바퀴상한·역할명단 + 도구설계원칙
├── collect_corpus.py
├── graph.py         # 기획 → 배치 → 조사 → 점검 → 종합
├── metrics.py        # 근거율·허위인용·편중·읽고안쓴문서
├── ablation.py       # 배정→역할→재위임→구역, 한 번에 하나씩 끄기
├── baseline.py       # 공정한 단일 에이전트 대조군
├── app.py            # streamlit 데모
├── output/           # runs.jsonl · ablation.json · reports/
└── REPORT.md
```
