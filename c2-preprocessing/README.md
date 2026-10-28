# C2 Preprocessing — 담당: 조아람

프레임 전처리와 과자 후보 박스 검출을 맡는다. 찾은 박스가 C3의 SAM 2 프롬프트가 된다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 4장 ②, 7.1

## 파일
| 파일 | 내용 |
|---|---|
| `app/preprocess.py` | `preprocess()`(리사이즈 → 블러 → CLAHE), `detect_boxes()`(Otsu 이진화 → 닫힘·열림 → 윤곽선 → 과자 아닌 덩어리 제거 → 박스) |
| `app/main.py` | 서버 (C1에서 받아 C3로 전달). 건드릴 필요 없음 |
| `tests/test_preprocess.py` | 박스 검출 테스트: 과자 모두 찾기, 반사된 밝은 영역·가는 띠 버리기, 화면 끝 과자 남기기 (`pytest`) |

## 과자가 아닌 덩어리 거르기 (`drop_non_cookies()`)
폰으로 모니터를 찍으면 반사 때문에 벨트 일부가 하얗게 뜬다. 화면 전체에 한 기준을 쓰는 Otsu 이진화는 이 밝은 배경을
과자로 잡아서, 큰 박스가 생기고 C3가 "불량"으로 판정한다. 시뮬레이터 과자는 모두 지름이 같다는 점을 이용해 걸러낸다.
- 면적이 그 프레임 덩어리들의 중간값보다 1.6배 넘게 크면 버린다 (반사된 배경, 붙어 버린 과자 두 개)
- 가로/세로 비가 0.4~2.5 밖이면 버린다 (벨트 테두리 띠). 반쪽 과자는 약 2, 화면 끝에 걸린 과자는 예외
- 시험 데이터 30장(과자 532개)에서 빠뜨리는 과자 없이 모두 찾는 것을 확인했다

## 테스트
```powershell
cd c2-preprocessing
pytest
```

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
