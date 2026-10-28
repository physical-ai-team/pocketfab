# C3 Inference — 담당: 박민성 (기술 지원: 김세광)

C2가 준 박스를 프롬프트로 SAM 2 마스크를 뽑고, 원형도·solidity·원 채움률로 정상/불량을 판정한다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 4장 ②③, 4.1, 7장

## 파일
| 파일 | 내용 |
|---|---|
| `app/judge.py` | 원형도·solidity·원 채움률 계산과 판정 규칙. **완성됨** (SAM 2 모드도 그대로 사용) |
| `app/opencv_segmenter.py` | `SEGMENTER=opencv` 기준선. 박스 안 OpenCV 윤곽선 |
| `app/main.py` | 서버. 한 번에 1장만 추론하고, 바쁘면 503 → Nginx가 다른 복제본으로 보냄 |
| `tests/test_judge.py` | 판정 규칙 테스트 (`pytest`) |

## 어디부터 채우면 되나
1. **SAM 2 설치** — 로컬 conda 환경에 CPU용 torch와 SAM 2를 설치하고, `python scripts/download_checkpoints.py tiny`로 체크포인트를 받는다.
2. **`app/sam2_segmenter.py` 새로 만들기** — `opencv_segmenter.segment()`와 **같은 입출력**으로 만든다.
   - 입력: BGR 이미지, `boxes`
   - 출력: `[{"box_id", "box", "contour"(픽셀 좌표 윤곽선), "mask_score"}]`
   - `SAM2ImagePredictor.set_image()` 1회 → 박스 전체를 한 번에 `predict(box=...)` → 마스크에서 `cv2.findContours`
3. **`app/main.py`의 `load_segmenter()`** — `sam2` 분기에서 위 모듈을 import한다 (torch import는 이 분기 안에서만).
4. **Dockerfile·requirements.txt** — torch(CPU 인덱스)와 SAM 2를 추가한다.

## 테스트
```powershell
cd c3-inference
pytest
```
