# CLAUDE.md

이 파일은 이 레포에서 작업할 때 참고할 프로젝트 맥락이다. 문서, 주석, 커밋 메시지, 답변은 **한국어**로 작성한다.

## 프로젝트 개요

**PocketFab (주머니 속의 스마트 팩토리)** — 팀 프로젝트(4인).
스마트폰 카메라로 컨베이어 위 과자를 찍고, **실시간 영상(30fps 입력)** 에서 불량(깨짐, 형태 변형)을 검출해
웹 대시보드에 표시한다. 정지 사진이 아니라 연속 프레임 스트림을 처리한다는 점이 핵심이다.

흐름: `스마트폰 → C1 → C2 → (Nginx) → C3×N → C4 → C1(대시보드)`

| 서비스 | 폴더 | 역할 | 담당 |
|---|---|---|---|
| C1 Gateway | `c1-gateway/` | QR 접속, 카메라 스트리밍 페이지, 대시보드, 백프레셔(`MAX_IN_FLIGHT`) | 이기태 |
| C2 Preprocessing | `c2-preprocessing/` | 리사이즈(종횡비 유지), 노이즈 제거, 밝기 보정, **OpenCV 과자 박스 검출** | 조아람 |
| C3 Inference | `c3-inference/` | 박스 프롬프트 → SAM 2 마스크 → 원형도 `4πA/P²` + solidity `A/A_hull` → 정상/불량 판정 | 박민성 |
| C4 Visualizer | `c4-visualizer/` | 원본 프레임에 판정 결과 마킹 → C1 전송 | 김세광 |
| Nginx | `nginx/` | 외부 HTTPS 진입점(:443 → C1) + C3 로드밸런싱(:8080 내부, `least_conn`) | 김세광 |

김세광(팀장)은 전체 통합(docker-compose, Nginx), C3 기술 지원, 성능 측정도 맡는다.
팀원 중 SW 전공자는 팀장 1명뿐이다. 하드웨어·전자상거래 전공자가 각 서비스를 맡으므로,
**코드는 단순하고 읽기 쉽게** 쓰고, 처음 보는 개념은 주석이나 서비스 README로 짧게 설명한다.

## 대상 과자 (2026-10-28 확정)

검사 대상은 **`tools/simulator/conveyor-cookie-sim.html`(시뮬레이터)이 만드는 합성 영상의 쿠키**다.
시연은 이 화면을 폰으로 찍고, 평가는 시뮬레이터가 저장한 프레임 + 정답 라벨을 쓴다.

- **과자**: 모두 **같은 지름의 원형 초코칩 쿠키**다 (지름은 설정값, 18~200px). 갈색 방사형 그라데이션, 어두운 초코칩 3~8개, 진한 가장자리 테두리. 그림자는 오른쪽 아래로 진다.
- **배경**: 어두운 벨트(`#2a2e31`, 바탕 `#14181b`), **1~6개 가로 라인**, 오른쪽 → 왼쪽 이동, 검사선은 화면 폭 34% 위치에 고정.
  벨트에는 옅은 세로 줄(클리트)과 "LINE 0N" 글자가 그려지고, 조명 불균일과 노이즈 효과를 켤 수 있다.
- **불량 4종과 잡는 지표** (이상적 마스크 측정값은 api-spec 7.3)

| 유형 | 모양 | 잡는 지표 |
|---|---|---|
| `bite` 한쪽 파임 | 가장자리가 원형으로 파임 (오목) | 오목 깊이 (solidity는 작은 결손을 놓침) |
| `cut` 단면 절단 | 직선으로 잘림 (볼록) | 원 채움률 (원형도·solidity로는 못 잡음) |
| `wedge` 조각 결손 | 중심까지 부채꼴로 빠짐 (오목) | 원형도 + solidity |
| `half` 반쪽 파손 | 반원 (볼록) | 원형도 |

- 정상 쿠키의 원형도는 픽셀 외곽 때문에 약 0.90이다 (1.0이 아님). 원형도·solidity만으로는 `bite`의 상당수와 `cut`을 놓쳐서
  원 채움률·오목 깊이를 추가했다 (api-spec 7.3, 시뮬레이터 4조건에서 불량 Recall 1.000).
- 라벨 JSON은 COCO와 비슷하다 (`bbox` = `[x, y, w, h]` 픽셀, `segmentation` = 180점 폴리곤 픽셀, 좌표는 저장된 PNG 픽셀 기준).
  `category`는 `round`/`deformed`, `defect_type`은 위 4종이다. 형식 분석은 performance-plan 7.1에 있다.
- 화면 양 끝에는 일부만 보이는 과자가 항상 있다. 정상 과자가 잘려 보여 불량으로 판정되므로 판정·평가에서 빼야 한다 (api-spec 7.4).
- 한 과자가 여러 프레임에 반복해서 찍히는 문제(중복 집계)와 입력 방식(폰 촬영 / 프레임 직접 입력)은 **결정 대기 중**이다.

## 핵심 문서 (작업 전에 먼저 읽을 것)

- [docs/api-spec.md](docs/api-spec.md) — 컨테이너 간 인터페이스 계약. **필드나 엔드포인트를 바꾸면 이 문서를 먼저 수정한다.**
- [docs/performance-plan.md](docs/performance-plan.md) — 성능 측정 계획과 CPU 환경의 Scale-out 한계

## 확정된 기술 결정

- **Python 3.11 + FastAPI/Uvicorn** (모든 서비스). C1 프론트엔드는 빌드 도구 없는 순수 HTML/JS.
- **통신**
  - 폰 → C1, C1 → 대시보드: WebSocket (`/ws/camera`, `/ws/dashboard`)
  - 컨테이너 간: **HTTP POST, `multipart/form-data`** (`meta` JSON + JPEG 파트), **동기식 체인**.
    C3에 연결이 열려 있는 시간이 곧 추론 시간이어야 Nginx `least_conn`이 동작하므로,
    C3를 202 즉시 응답으로 바꾸지 않는다.
- **좌표는 모두 0~1로 정규화.** C2는 크롭하지 않고 종횡비를 유지해 리사이즈한다.
- **SAM 2는 자동 분할을 쓰지 않고 박스 프롬프트(`SAM2ImagePredictor`)를 쓴다.** 박스는 C2가 OpenCV로 검출한다.
  `boxes`가 비어 있으면 C3 호출을 건너뛴다.
- **판정**: `mask_score < MASK_SCORE_MIN`이면 `uncertain`, 아니면 원형도(`CIRCULARITY_THRESHOLD`), solidity(`SOLIDITY_THRESHOLD`),
  원 채움률(`FILL_RATIO_THRESHOLD`), 오목 깊이(`CONCAVITY_THRESHOLD`) 중 **하나라도** 벗어나면 `defect`이고
  벗어난 지표를 `defect_reasons`에, 추정 유형을 `defect_type`에 기록한다.
  규칙의 기준은 api-spec 4.1.
- **`SEGMENTER=sam2|opencv`**: opencv 모드는 SAM 2 대신 OpenCV 윤곽선을 쓰는 기준선이다. 출력 형식과 판정 규칙은 같고,
  torch를 import하지 않아야 한다. 따라서 C3에서 torch/SAM 2 import는 sam2 모드 경로 안에서만 한다.
- 모든 메시지에 `session_id`, `frame_id`, `timings`(epoch ms)를 담고, 각 단계는 받은 meta를 보존한 채 필드를 추가한다.
  C1은 늦게 도착한 오래된 `frame_id`를 표시하지 않는다.
- **HTTPS 필수**: 브라우저는 보안 연결에서만 카메라(`getUserMedia`)를 허용한다. Nginx가 자체 서명 인증서로 TLS를 처리한다.

## 실행 환경 제약

- 개발 PC: AMD Ryzen 7 5700U (8코어/16스레드), RAM 14GB, **NVIDIA GPU 없음 → CPU 추론이 기본**.
  Windows 11 + Docker Desktop(WSL2). 셸은 PowerShell.
- **CPU/GPU 전환은 환경변수로만 한다** (`DEVICE=cpu|cuda`, `SAM2_MODEL`, `TORCH_NUM_THREADS` 등).
  코드에 `"cuda"`나 `.cuda()`를 하드코딩하지 않는다. GPU는 `docker-compose.gpu.yml` 오버라이드로 전환할 계획이다.
- C3 복제본들은 같은 CPU 코어를 나눠 쓴다. `복제본 수 × TORCH_NUM_THREADS ≤ 8`을 지키고,
  `OMP_NUM_THREADS`도 같은 값으로 설정한다.
- CPU에서는 30fps를 모두 처리하지 못한다. 처리 못 한 프레임은 C1에서 버려서 지연이 쌓이지 않게 하는 것이 정상 동작이다.

## 개발 환경

- 로컬: conda 환경 `pocketfab` (`environment.yml`)
  ```powershell
  conda env create -f environment.yml    # 최초 1회
  conda activate pocketfab
  conda env update -f environment.yml --prune   # 의존성 변경 후
  ```
  패키지를 추가하면 `environment.yml`에 반영한다. torch/SAM 2는 용량이 커서 C3 개발을 시작할 때 추가한다.
- Docker: 각 서비스는 conda가 아니라 자기 폴더의 `requirements.txt` + pip을 쓴다 (기본 이미지 `python:3.11-slim`).
- 서비스 내부 포트는 모두 `8000`이고, 서비스 이름(`c1-gateway` 등)으로 서로 호출한다.
- **DB는 쓰지 않는다.** 큰 파일은 git 밖에서 관리한다.
  - SAM 2 체크포인트: `python scripts/download_checkpoints.py [tiny|small]` → `checkpoints/` (git 제외).
    Docker 이미지에 넣지 않고 C3에 볼륨 마운트한다 (`SAM2_CHECKPOINT_DIR`). 지원 모델은 tiny, small뿐이다.
  - 테스트 영상·이미지: Google Drive로 공유하고 `data/`에 둔다 (git 제외). 폴더 구조와 파일 이름 규칙은 README의 "데이터 공유" 섹션을 따른다.

## 브랜치·PR 규칙

**main에는 직접 커밋·push하지 않는다** (GitHub 보호 규칙으로 PR로만 합칠 수 있다). 모든 작업은 브랜치에서 하고 PR로 올린다.

- 브랜치 이름: `<type>/<scope>-<요약>` — 영문 소문자, 단어는 `-`로 연결
  - `type`: `feat`(기능) `fix`(버그) `docs`(문서) `refactor`(동작 변화 없는 정리) `test` `perf`(성능) `chore`(설정·빌드·의존성)
  - `scope`: `c1` `c2` `c3` `c4` `nginx` `infra`(compose, 여러 서비스 공통) `tools` `sim`(시뮬레이터) — 문서 전반이면 생략 가능
  - 예: `feat/c1-qr`, `fix/c3-mask`, `perf/c3-threads`, `chore/infra-compose-gpu`, `docs/api-spec`
- 커밋 메시지: `<type>: <한국어 요약>` (예: `fix: C3 마스크 좌표 정규화 오류 수정`)
- 순서
  ```powershell
  git switch main; git pull
  git switch -c feat/c3-sam2-segmenter
  # 작업 → 커밋
  git push -u origin feat/c3-sam2-segmenter
  ```
  push 후 PR을 만든다. 이 PC에는 `gh`가 없으므로 PR 생성 링크
  `https://github.com/physical-ai-team/pocketfab/compare/main...<브랜치>?expand=1`과 PR 본문을 사용자에게 준다.
- PR 하나에는 주제 하나만 담는다. 범위가 다른 작업은 브랜치를 나눈다.
- PR 본문은 `.github/pull_request_template.md` 형식(변경 내용 / 확인 방법 / 스펙 변경 여부)을 따른다.
- api-spec이나 환경변수를 바꾸는 PR은 영향을 받는 서비스 담당자를 리뷰어로 지정한다.
- 합치기는 **Squash and merge**로 하고, 합친 브랜치는 삭제한다.

## 작업 규칙

- 한 서비스의 변경이 다른 서비스에 영향을 주면(API 형식, 환경변수) `docs/api-spec.md`도 함께 수정한다.
- 판정 임계값 같은 튜닝 값은 환경변수로 노출하고, 기본값은 api-spec 6장의 표에 적는다.
- 성능에 영향을 주는 변경을 했으면 `docs/performance-plan.md` 결과표 기준으로 다시 측정할 수 있게 한다.
- 현재 상태: 뼈대 파이프라인이 동작한다 (`docker compose up` → `tools/fake_camera.py` 또는 폰으로 확인).
  남은 핵심 작업은 각 서비스 README의 "어디부터 채우면 되나"에 있다:
  C2 `app/preprocess.py`, C3 `app/sam2_segmenter.py`(신규), C4 `app/draw.py`.
- 서비스의 HTTP 처리(`main.py`)와 영상 처리 로직(`preprocess.py`, `*_segmenter.py`, `judge.py`, `draw.py`)은 분리한다.
  비전공 담당자는 로직 파일만 고치면 되게 하고, OpenCV/torch 연산은 `run_in_threadpool`로 호출한다.
- 변경 후 확인: `docker compose up -d --build --scale c3-inference=2` → `python tools/fake_camera.py --seconds 10`
  → 처리 fps가 0보다 크고 `https://localhost/v1/metrics`의 `replicas`에 복제본이 고르게 나오는지 본다.
  C3 판정 로직은 `c3-inference`에서 `pytest`.
- 대상 과자는 시뮬레이터 쿠키로 확정됐다 (위 "대상 과자" 절). 시뮬레이터의 `makeShape()`가 바뀌면
  `tools/simulator/shape_metrics.py`도 같이 고치고 api-spec 7.3의 측정표를 다시 만든다.
