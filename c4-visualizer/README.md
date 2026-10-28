# C4 Visualizer — 담당: 김세광

C3의 판정 결과를 원본 프레임에 그려 C1으로 보낸다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 4장 ③④

## 파일
| 파일 | 내용 |
|---|---|
| `app/draw.py` | **채울 곳.** 프레임에 결과 그리기. 지금은 텍스트만 표시 |
| `app/main.py` | 서버 (C3에서 받아 C1으로 전달). 건드릴 필요 없음 |

## 어디부터 채우면 되나
1. **`render()`에 윤곽선 그리기** — `objects[].polygon`(0~1 좌표)에 이미지 크기를 곱해 `cv2.polylines`로 그린다.
   색: 불량 빨강 / 정상 초록 / 판단불가 노랑 (`COLORS`)
2. 불량이면 `defect_reasons`(원형도/solidity 중 무엇 때문인지)를 라벨에 표시한다.
3. 프레임 상단 요약 바를 시연용으로 다듬는다.
