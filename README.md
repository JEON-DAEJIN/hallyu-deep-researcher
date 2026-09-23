# hallyu-deep-researcher

AIFFEL N043 과제 — "나만의 딥리서치 에이전트 만들기". N042에서 배운 코디네이터+조사관+편집자 6노드 LangGraph 리서치 파이프라인을, **"한류 콘텐츠 산업의 확산"**이라는 나만의 주제로 처음부터 다시 설계해서 제출한다.

> 진행 상황과 설계 근거는 [PLAN.md](PLAN.md)를, 최종 제출 보고서는 [REPORT.md](REPORT.md)를 참고.

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
