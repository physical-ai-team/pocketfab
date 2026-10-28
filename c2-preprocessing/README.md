# C2 Preprocessing — 담당: 조아람

프레임 전처리와 과자 후보 박스 검출을 맡는다. 찾은 박스가 C3의 SAM 2 프롬프트가 된다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 4장 ②, 7.1

## 파일
| 파일 | 내용 |
|---|---|
| `app/preprocess.py` | **채울 곳.** `preprocess()`, `detect_boxes()` 두 함수 |
| `app/main.py` | 서버 (C1에서 받아 C3로 전달). 건드릴 필요 없음 |

## 어디부터 채우면 되나
지금은 프레임을 그대로 넘기고 박스는 빈 목록이다. `app/preprocess.py`의 TODO 순서대로 채운다.
1. **`preprocess()`** — 리사이즈(종횡비 유지, 크롭 금지) → 노이즈 제거 → 밝기 보정
2. **`detect_boxes()`** — OpenCV로 과자 위치를 찾아 0~1 정규화 박스로 반환
3. 박스 검출이 안정되면 `.env`에서 `SKIP_C3_IF_NO_BOXES=true`로 바꾼다 (과자 없는 프레임은 C3 생략)

## 혼자 확인하기
서버 없이 함수만 시험하려면 시뮬레이터 자동 캡처로 만든 `data/sim/calib/`의 프레임으로 이렇게 해 본다
(PNG도 `cv2.imdecode`로 그대로 읽힌다). 같은 번호의 `.json`에 있는 `annotations[].bbox`가 정답 박스이므로 결과와 비교할 수 있다.
```python
from app.preprocess import preprocess, detect_boxes
frame = open("../data/sim/calib/0001.png", "rb").read()
img, size, info = preprocess(frame, {"w": 1008, "h": 450})   # 크기는 같은 번호 JSON의 image.width/height
print(size, info, detect_boxes(img, size))
```
전체로 확인하려면 `docker compose up --build` 후 대시보드에서 C3가 그린 라벨이 과자 위에 뜨는지 본다.
