# 컨베이어 과자 검사 시뮬레이터

PocketFab의 검사 대상(같은 크기 원형 초코칩 쿠키)을 만드는 합성 영상 생성기입니다.

| 파일 | 내용 |
|---|---|
| `conveyor-cookie-sim.html` | 시뮬레이터. Chrome으로 열면 바로 동작합니다 |
| `shape_metrics.py` | 불량 유형별 원형도·solidity 분포 측정 (`python tools/simulator/shape_metrics.py`) |

- 사용법, 특히 **프레임(S)과 라벨(L) 저장 절차**: 루트 [README의 "시뮬레이터 사용법"](../../README.md#시뮬레이터-사용법)
- 라벨 JSON 형식: [docs/performance-plan.md](../../docs/performance-plan.md) 7.1
- 측정 결과와 판정 지표: [docs/api-spec.md](../../docs/api-spec.md) 7.3

시뮬레이터를 고칠 때는 `sim` scope로 브랜치를 만듭니다 (예: `feat/sim-auto-capture`).
형상 생성 코드(`makeShape()`)를 바꾸면 `shape_metrics.py`의 `make_radius()`도 같이 고쳐야 합니다.
