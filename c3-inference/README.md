# C3 Inference — 담당: 박민성 (기술 지원: 김세광)

C2가 준 박스를 프롬프트로 SAM 2 마스크를 뽑고, 원형도·solidity·원 채움률·오목 깊이로 정상/불량과 불량 유형을 판정한다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 4장 ②③, 4.1, 7장

## 파일
| 파일 | 내용 |
|---|---|
| `app/judge.py` | 네 지표 계산, 판정 규칙, 불량 유형(`defect_type`) 추정. 두 세그멘터가 함께 쓴다 |
| `app/sam2_segmenter.py` | `SEGMENTER=sam2`. 이미지 인코더 1회(`set_image`) → 박스 전체를 한 번에 디코딩 → 마스크 윤곽선. torch는 이 파일에서만 import |
| `app/opencv_segmenter.py` | `SEGMENTER=opencv` 기준선. 박스 안 OpenCV 윤곽선 (torch 불필요) |
| `app/main.py` | 서버. 한 번에 1장만 추론하고, 바쁘면 503 → Nginx가 다른 복제본으로 보냄 |
| `tests/test_judge.py` | 판정 규칙 테스트 |
| `tests/test_sam2_segmenter.py` | SAM 2 테스트. torch·SAM 2·체크포인트가 없으면 건너뛴다 |

## 로컬 테스트 환경 (Docker 없이)
판정 로직과 SAM 2는 Docker 없이 로컬 Python에서 테스트한다. 레포 루트의 PowerShell에서 실행한다.
```powershell
conda activate pocketfab                         # 루트 README "실행 방법" 1
python scripts/download_checkpoints.py tiny     # checkpoints/sam2.1_hiera_tiny.pt (156MB)
# CPU용 torch → SAM 2 순서로 설치 (약 1GB). SAM 2는 CUDA 확장 빌드를 끄고 설치한다
pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu
pip install setuptools wheel
$env:SAM2_BUILD_CUDA = "0"; $env:SAM2_BUILD_ALLOW_ERRORS = "1"; pip install --no-build-isolation -r c3-inference/requirements.txt
```
판정 규칙만 고칠 때는 torch·SAM 2 없이 `pytest`만 돌려도 된다 (SAM 2 테스트는 자동으로 건너뜀).

## 테스트
```powershell
cd c3-inference
$env:SAM2_CHECKPOINT_DIR = "../checkpoints"; pytest
```

## 판정 정확도 채점
시뮬레이터 프레임(`frame_*.png`)과 정답 라벨(`frame_*.json`)이 있는 폴더로 C3 판정을 채점한다 (레포 루트에서).
```powershell
python tools/c3_eval.py data/sim/calib                    # OpenCV 모드
python tools/c3_eval.py data/sim/calib --segmenter sam2    # SAM 2 모드 (checkpoints/ 필요)
python tools/c3_eval.py data/sim/calib --masks gt          # 정답 폴리곤으로 판정 규칙만
```
보정(`calib`)·확인(`test`) 절차: [performance-plan 3~4장](../docs/performance-plan.md). 임계값 근거: [api-spec 7.3](../docs/api-spec.md)
