# PocketFab — 주머니 속의 스마트 팩토리

스마트폰 카메라 하나로 컨베이어 위 과자의 불량(깨짐, 형태 변형)을 **실시간 영상(30fps 입력)** 에서 검출하는 시스템입니다.
SAM 2로 과자 마스크를 추출하고, OpenCV로 원형도를 계산해 정상/불량을 판정한 뒤, 결과를 웹 대시보드에 표시합니다.

> 현재 상태: **뼈대 파이프라인 완성**. 폰 카메라 → C1 → C2 → C3 → C4 → 대시보드까지 프레임이 흐릅니다.
> C2 전처리·박스 검출, C3 SAM 2 추론, C4 윤곽선 그리기는 각 담당자가 채울 차례입니다 (각 폴더의 README 참고).

## 주요 특징

- **설치 없는 카메라 연결**: QR코드를 찍으면 스마트폰 브라우저가 바로 카메라 스트리밍을 시작합니다.
- **SAM 2 기반 정밀 분할**: OpenCV로 과자 위치(박스)를 먼저 찾고, 그 박스를 SAM 2 프롬프트로 넘겨 마스크를 얻습니다.
- **형태 지표 판정**: 네 지표 중 **하나라도** 임계값을 벗어나면 불량으로 판정하고, 불량 유형(bite/cut/wedge/half)도 추정합니다.
  - 원형도 `Circularity = 4π × Area / Perimeter²` (완전한 원 = 1.0): 반쪽 파손 같은 전체 형태 변형을 잡습니다.
  - Solidity `Area / 볼록껍질 면적` (오목한 곳이 없으면 1.0): 가장자리가 파인 깨짐을 잡습니다.
  - 원 채움률 `Area / 최소 외접원 면적` (완전한 원 = 1.0): 직선으로 잘린 것처럼 볼록하게 빠진 결손을 잡습니다.
  - 오목 깊이 `최대 오목 깊이 / 외접원 반지름` (오목한 곳이 없으면 0): 작게 파인 결손까지 잡습니다.
- **OpenCV 기준선 모드**: `SEGMENTER=opencv`로 SAM 2 없이 OpenCV 윤곽선만으로 판정할 수 있습니다. 성능·정확도 비교와 시연 대비용입니다.
- **마이크로서비스 + Scale-out**: 단계마다 Docker 컨테이너를 따로 두고, 추론(C3)은 Nginx 뒤에서 여러 개로 복제합니다.

## 대상 과자

**[컨베이어 과자 검사 시뮬레이터](tools/simulator/conveyor-cookie-sim.html)가 만드는 합성 영상의 쿠키**가 검사 대상입니다 (2026-10-28 확정).

- **과자**: 모두 **같은 크기의 원형 초코칩 쿠키**입니다. 갈색 그라데이션에 어두운 초코칩이 박혀 있고, 가장자리는 진한 테두리입니다.
- **배경**: 어두운 고무 벨트(`#2a2e31`) 위에 1~6개 라인이 가로로 놓이고, 과자는 오른쪽에서 왼쪽으로 흐릅니다.
  화면 폭 34% 위치에 고정된 **검사선**이 있습니다.
- **불량 4가지**: 정상 원에서 한 덩어리를 잘라낸 형태입니다. 남은 외곽은 원래 원호 그대로입니다.

| 유형 | 모양 | 볼록/오목 | 잡는 지표 |
|---|---|---|---|
| `bite` 한쪽 파임 | 가장자리가 원 모양으로 물어뜯김 | 오목 | **오목 깊이** (작은 결손도), solidity·원 채움률 (큰 결손) |
| `cut` 단면 절단 | 가장자리가 직선으로 잘림 | 볼록 | **원 채움률** (원형도·solidity는 정상과 겹침) |
| `wedge` 조각 결손 | 중심까지 부채꼴로 빠짐 | 오목 | **네 지표 모두** 크게 벗어남 |
| `half` 반쪽 파손 | 반원 | 볼록 | **원형도, 원 채움률** (solidity는 정상과 같음) |

> 현재 기본 임계값(원형도 0.80 / solidity 0.95 / 원 채움률 0.93 / 오목 깊이 0.06)으로 시뮬레이터 4조건을 채점하면,
> SAM 2 마스크에서 불량 Recall·Precision·유형 정확도가 모두 1.000, 마스크 IoU는 0.97입니다 (박민성 측정).
> 정상 쿠키도 픽셀 외곽 때문에 원형도가 1.0이 아니라 약 0.90으로 나옵니다.
> 측정값은 [docs/api-spec.md](docs/api-spec.md) 7.3에 있고, `python tools/c3_eval.py`·`python tools/simulator/shape_metrics.py`로 다시 잴 수 있습니다.

## 아키텍처

```
[Smartphone camera]                         [Dashboard browser]
        │ WSS (JPEG)                                ▲ WSS (JSON)
        ▼                                           │
     Nginx :443  (HTTPS entry, proxy to C1) ────────┘
        │
        ▼
    C1 Gateway ──①──▶ C2 Preprocessing ──②──▶ Nginx :8080 (LB) ──▶ C3 Inference ×N
        ▲                                                               │
        │                                                               ③
        └───────────────④─────────────── C4 Visualizer ◀────────────────┘
```

| 컨테이너 | 역할 |
|---|---|
| **C1 Gateway** | QR코드 접속, 스마트폰 카메라 페이지, 결과 대시보드, 프레임 유입 제어(백프레셔) |
| **C2 Preprocessing** | 리사이즈, 노이즈 제거, 밝기 보정, OpenCV로 과자 후보 박스 검출 |
| **C3 Inference** | 박스 프롬프트로 SAM 2 마스크 추출, 원형도·solidity 계산, 정상/불량 판정 |
| **C4 Visualizer** | 판정 결과를 원본 프레임에 마킹해 C1로 전송. 과자를 추적해 검사선 통과 시 1회 집계(다수결 판정). **복제하지 않음** |
| **Nginx** | 외부 HTTPS 진입점(C1 프록시) + C3 복제본 로드밸런싱(`least_conn`) |

자세한 데이터 형식은 [docs/api-spec.md](docs/api-spec.md)를 참고하세요.

## 기술 스택

| 분류 | 사용 기술 |
|---|---|
| 언어 | Python 3.11 |
| 서버 | FastAPI + Uvicorn |
| 프론트엔드 | 순수 HTML / JavaScript (`getUserMedia`, Canvas, WebSocket) |
| 영상 처리 | OpenCV, NumPy |
| AI | PyTorch, SAM 2.1 (`hiera_tiny` 기본) |
| 통신 | 스마트폰·대시보드 ↔ C1: WebSocket / 컨테이너 간: HTTP POST (multipart, 동기식 체인) |
| 인프라 | Docker, docker-compose, Nginx |
| 로컬 개발 환경 | conda (`environment.yml`) |

**연산 장치**: 기본값은 **CPU**입니다 (개발 PC에 NVIDIA GPU가 없음). `DEVICE=cuda` 환경변수와
GPU용 compose 오버라이드로 코드 수정 없이 GPU로 전환할 수 있게 설계합니다.

## 역할 분배

| 이름 | 전공 | 담당 |
|---|---|---|
| 김세광 (팀장) | SW·AI | C4 Visualizer, 전체 통합(docker-compose, Nginx), C3 기술 지원, 성능 측정 |
| 박민성 | 하드웨어 | C3 Inference |
| 이기태 | 하드웨어 | C1 Gateway, 촬영 환경 구축, 테스트 데이터 수집 |
| 조아람 | 전자상거래 | C2 Preprocessing |

## 작업 규칙 (브랜치·PR)

**main에는 직접 push하지 않습니다.** 모든 작업은 브랜치에서 하고 Pull Request로 합칩니다.

### 브랜치 이름

`<type>/<scope>-<요약>` — 영문 소문자, 단어는 `-`로 연결합니다.

| type | 언제 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서만 변경 |
| `refactor` | 동작은 같고 코드만 정리 |
| `test` | 테스트 추가·수정 |
| `perf` | 성능 개선·측정 |
| `chore` | 설정, 빌드, 의존성 |

`scope`: `c1` `c2` `c3` `c4` `nginx` `infra`(compose 등 공통) `tools` `sim`(시뮬레이터). 문서 전반이면 생략할 수 있습니다.

예: `feat/c1-qr`, `fix/c3-mask`, `feat/c2-box-detect`, `perf/c3-threads`, `docs/api-spec`

### 작업 순서

```powershell
git switch main; git pull                  # 1. 최신 main에서 시작
git switch -c feat/c2-box-detect           # 2. 브랜치 만들기
git add -A; git commit -m "feat: C2 박스 검출 구현"   # 3. 작업하고 커밋
git push -u origin feat/c2-box-detect      # 4. 올리기
```

5. GitHub에서 **Compare & pull request**를 눌러 PR을 만들고, 템플릿(변경 내용 / 확인 방법 / 스펙 변경 여부)을 채웁니다.
6. 리뷰를 1명 이상 받은 뒤 **Squash and merge**로 합치고 브랜치를 삭제합니다.

- PR 하나에는 주제 하나만 담습니다.
- 커밋 메시지는 `<type>: <한국어 요약>` 형식입니다.
- API 스펙(`docs/api-spec.md`)이나 환경변수를 바꾸는 PR은 영향을 받는 서비스 담당자에게 리뷰를 요청합니다.

## 폴더 구조

```
pocketfab/
├── c1-gateway/          # C1 (이기태)
├── c2-preprocessing/    # C2 (조아람)
├── c3-inference/        # C3 (박민성)
├── c4-visualizer/       # C4 (김세광)
├── nginx/               # Nginx 설정, 자체 서명 인증서 (김세광)
├── docs/
│   ├── api-spec.md          # 컨테이너 간 API 스펙
│   └── performance-plan.md  # 성능 측정 계획
├── scripts/
│   └── download_checkpoints.py  # SAM 2 체크포인트 다운로드
├── tools/
│   ├── fake_camera.py   # 폰 대신 프레임을 보내는 가짜 카메라 (테스트·시연 예비)
│   ├── c3_eval.py       # 시뮬레이터 정답 라벨로 C3 판정 채점
│   └── simulator/       # 대상 과자 시뮬레이터(HTML), 형상 지표 분석 스크립트
├── checkpoints/         # SAM 2 체크포인트 (git 제외, 스크립트로 받음)
├── data/                # 평가 데이터 (git 제외, 각자 시뮬레이터 자동 캡처로 생성)
├── docker-compose.yml
├── .env.example         # 설정 예시 (.env로 복사해서 사용)
├── environment.yml      # 로컬 개발용 conda 환경
├── CLAUDE.md
└── README.md
```

각 서비스 폴더는 `app/`(소스), `Dockerfile`, `requirements.txt`, `README.md`(담당자가 어디부터 채우면 되는지)로 구성됩니다.

## 실행 방법

준비물: Docker Desktop, conda. 명령은 모두 레포 루트의 PowerShell에서 실행합니다.

### 1. 로컬 개발 환경 (conda)

```powershell
conda env create -f environment.yml
conda activate pocketfab
```

### 2. SAM 2 체크포인트 다운로드

모델 파일은 용량이 커서 git에 올리지 않습니다. Meta 공식 경로에서 `checkpoints/` 폴더로 받습니다.
표준 라이브러리만 사용하므로 별도 패키지 설치 없이 실행할 수 있습니다.

```powershell
conda activate pocketfab
python scripts/download_checkpoints.py           # tiny + small 모두
python scripts/download_checkpoints.py tiny      # tiny만
python scripts/download_checkpoints.py --force small   # 다시 받기
```

| 모델 | 파일 | 용량 | 용도 |
|---|---|---|---|
| `tiny` | `checkpoints/sam2.1_hiera_tiny.pt` | 약 149MB | 기본값 (CPU 실시간용) |
| `small` | `checkpoints/sam2.1_hiera_small.pt` | 약 176MB | 정확도 비교 실험용 |

- 이미 받은 파일은 크기를 비교해서 건너뜁니다. 다운로드가 중간에 끊기면 `.part` 파일만 남으니 다시 실행하면 됩니다.
- C3 컨테이너는 이 폴더를 볼륨으로 마운트해서 사용할 예정입니다 (`SAM2_CHECKPOINT_DIR`).

> 지금은 `SEGMENTER=opencv`라서 체크포인트 없이도 실행됩니다.

### 3. 설정 파일 만들기 (최초 1회)

```powershell
Copy-Item .env.example .env
# PC의 LAN IP 확인 (Wi-Fi 어댑터의 IPv4 주소)
Get-NetIPAddress -AddressFamily IPv4 | Where-Object InterfaceAlias -match 'Wi-Fi' | Select-Object IPAddress
```
`.env`를 열어 `PUBLIC_HOST`를 위 IP로 바꿉니다. 이 IP가 QR코드와 HTTPS 인증서에 들어갑니다.
(PC에서만 시험할 때는 `.env` 없이 실행해도 되며, 이때는 `localhost`로 동작합니다.)

### 4. 전체 시스템 실행 (Docker)

```powershell
docker compose up -d --build --scale c3-inference=2   # C3를 2개로 복제
docker compose ps                                     # 6개 컨테이너가 Up인지 확인
docker compose logs -f c3-inference                   # 특정 서비스 로그 보기
docker compose up -d --scale c3-inference=4           # 실행 중에 복제본 수 변경 (C3만! C4는 복제 금지)
docker compose down                                   # 종료
```

`.env`를 바꾼 뒤에는 `docker compose up -d`를 다시 실행하면 바뀐 서비스만 재시작됩니다.

### 5. 접속

**폰 없이 확인하기**
```powershell
conda activate pocketfab
python tools/fake_camera.py --seconds 20
```
PC 브라우저에서 `https://localhost/`을 열면 합성 영상(`fake N`)이 대시보드에 흘러갑니다.
`https://localhost/v1/metrics`에서 누적 통계(처리 수, 드롭 이유, 복제본별 처리 수)를 볼 수 있습니다.

**스마트폰으로 접속하기**
1. PC 브라우저에서 `https://<PUBLIC_HOST>/` 대시보드를 열고 QR코드를 확인합니다.
2. 같은 Wi-Fi에 연결된 스마트폰으로 QR을 찍습니다.
3. 자체 서명 인증서 경고가 뜨면 "고급 → 계속 진행"을 누릅니다. 브라우저는 HTTPS에서만 카메라를 허용합니다.
4. "스트리밍 시작"을 누르고 카메라 권한을 허용하면 대시보드에 결과가 표시됩니다.

> 폰에서 접속이 안 되면 Windows 방화벽에서 443 포트 인바운드를 허용합니다 (관리자 PowerShell):
> `New-NetFirewallRule -DisplayName "PocketFab HTTPS" -Direction Inbound -Protocol TCP -LocalPort 443 -Action Allow`

### 6. 주요 설정 (`.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PUBLIC_HOST` | `localhost` | 폰이 접속할 PC의 LAN IP |
| `MAX_IN_FLIGHT` | (비움 = 자동) | 동시에 처리 중일 수 있는 프레임 수. 비우면 C3 복제본 수 × `IN_FLIGHT_PER_REPLICA`(기본 1)로 자동 결정되고, `--scale`을 바꾸면 5초 안에 따라갑니다. 값을 넣으면 그 값을 우선합니다 |
| `SEGMENTER` | `opencv` | `opencv` / `sam2`. 시연은 `sam2` (체크포인트 필요, 프레임당 약 3초) |
| `SKIP_C3_IF_NO_BOXES` | `false` | C2 박스 검출 구현 후 `true`로 변경 |
| `SIMULATED_INFER_MS` | `0` | C3 추론 시간을 흉내 내는 지연. 로드밸런싱 시험용 |

전체 목록은 [docs/api-spec.md](docs/api-spec.md) 6장을 참고하세요.

### 7. GPU 환경으로 전환 (예정)

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

## 줌 시연 방법

최종 확인은 줌 온라인 수업에서의 실시간 시연입니다. **폰으로 모니터의 시뮬레이터를 찍고, 대시보드를 화면 공유**해서 보여줍니다.

### 준비물

| 무엇 | 역할 |
|---|---|
| PC (Docker 실행) | 파이프라인 전체, 줌 접속, 화면 공유 |
| **모니터 2대** | 모니터 1: 시뮬레이터 (폰이 촬영) / 모니터 2: 대시보드 (줌으로 공유) |
| 스마트폰 + 거치대 | 카메라 전용. **이 폰으로 줌에 접속하지 않습니다** |
| 같은 Wi-Fi | 폰과 PC가 같은 네트워크에 있어야 폰이 `https://<PUBLIC_HOST>`에 접속됩니다 |

### 하루 전 준비

1. `.env`에 `PUBLIC_HOST`(PC의 LAN IP)와 `SEGMENTER=sam2`를 넣고, `python scripts/download_checkpoints.py tiny`로 체크포인트를 받습니다.
2. `docker compose up -d --build --scale c3-inference=2`로 미리 빌드합니다. 처음 빌드는 torch 설치 때문에 오래 걸립니다.
3. 폰으로 QR을 찍어 한 번 접속하고, **인증서 경고를 미리 넘겨 둡니다** ("고급 → 계속 진행"). 방화벽 443 허용도 이때 확인합니다 (실행 방법 5).
4. 아래 순서대로 **처음부터 끝까지 한 번 리허설**합니다. 대시보드 "누적 집계"가 시뮬레이터 카운터와 비슷하게 올라가는지 봅니다.
5. (선택) 예비 녹화 영상 `data/videos/demo.mp4`를 만들어 둡니다 ([performance-plan](docs/performance-plan.md) 5장).

### 시뮬레이터 시연 설정

- **정답 외곽선 표시는 끕니다.** 화면의 외곽선이 폰 카메라에 그대로 찍혀서 검출에 섞입니다.
- **컨베이어 속도를 30~40px/s로 낮춥니다** (`SEGMENTER=sam2`일 때). SAM 2는 CPU에서 프레임당 약 3초라 처리 fps가 1보다 낮습니다.
  C4 집계는 처리된 프레임 사이에 과자가 과자 간격의 약 80%보다 덜 움직여야 정확합니다 (api-spec 4.2).
  `SEGMENTER=opencv`로 시연하면 기본 속도(130px/s)를 써도 됩니다.
- 라인 수, 지름, 결손 크기는 평가 데이터(`data/sim/calib`, `test`)를 만들 때와 같게 둡니다. 그래야 보정한 임계값이 그대로 맞습니다.

### 시연 순서

1. **서버 켜기**: `docker compose up -d --scale c3-inference=2` → `docker compose ps`로 6개 컨테이너가 Up인지 확인합니다.
2. **모니터 1**: Chrome으로 `tools/simulator/conveyor-cookie-sim.html`을 열고 **F11(전체 화면)** 을 누른 뒤 재생합니다.
3. **모니터 2**: Chrome으로 대시보드 `https://<PUBLIC_HOST>/`을 엽니다.
4. **폰**: 거치대에 고정하고 대시보드의 QR을 찍어 카메라 페이지를 엽니다. 모니터 1과 **정면으로 평행하게**, 컨베이어 영역이 화면에 꽉 차게 맞춘 뒤 "스트리밍 시작"을 누릅니다.
5. **줌**: "화면 공유"에서 **모니터 2(대시보드)** 를 고릅니다. 대시보드의 결과 영상에 폰이 찍은 원본 프레임이 들어 있어서, 시뮬레이터 화면을 따로 공유하지 않아도 됩니다.
6. **설명할 것**
   - 결과 영상 위의 불량 표시와 불량 유형(`bite`/`cut`/`wedge`/`half`)
   - "누적 집계"(과자 단위)와 시뮬레이터 카운터(검사 통과 / 불량) 비교
   - "처리 현황 → C3 복제본별 fps": 요청이 Nginx를 거쳐 여러 C3로 나뉘는 모습 (Scale-out)
7. **끝내기**: 폰에서 "중지"를 누르고 `docker compose down`을 실행합니다.

### 폰 연결이 안 될 때 (예비 전환)

1. 폰 카메라 페이지에서 **"중지"를 먼저 누릅니다.** 폰과 예비 영상이 동시에 들어오면 대시보드에 두 세션이 섞여 보입니다.
2. PC의 PowerShell에서 예비 영상을 넣습니다. 영상이 끝나면 처음부터 반복 재생됩니다.
   ```powershell
   conda activate pocketfab
   python tools/fake_camera.py --video data/videos/demo.mp4 --seconds 600
   ```
   예비 영상이 없으면 `python tools/fake_camera.py --seconds 600`(합성 영상)로 파이프라인이 동작하는 것만 보여줍니다.
3. 대시보드는 그대로 공유하면 됩니다. 새 세션이라 **"누적 집계"는 0부터 다시** 셉니다.

자주 막히는 곳
- **폰에서 페이지가 안 열림**: 같은 Wi-Fi인지, `.env`의 `PUBLIC_HOST`가 지금 IP인지, 방화벽 443이 열렸는지 확인합니다.
  학교·공용 Wi-Fi는 기기끼리 통신을 막는 경우가 많습니다. 이때는 **폰 핫스팟에 PC를 연결**하고, 바뀐 IP로 `PUBLIC_HOST`를 고친 뒤 `docker compose up -d`를 실행합니다.
  인증서와 QR은 자동으로 다시 만들어지므로, 폰에서 인증서 경고를 한 번 더 넘깁니다.
- **카메라가 안 켜짐**: 주소가 `https://`인지, 브라우저의 카메라 권한이 허용됐는지 확인합니다.
- **집계가 이상함**: 시뮬레이터 속도를 더 낮추거나, 폰 화면에 컨베이어만 꽉 차게 다시 맞춥니다 (검사선 위치 `COUNT_LINE_X`).

## 입력 방식 (3단계)

같은 시뮬레이터를 쓰더라도 **무엇을 확인하느냐에 따라 입력을 다르게** 넣습니다.

| 단계 | 확인하는 것 | 입력 | 경로 | 정답과 비교 |
|---|---|---|---|---|
| **① 정확도 평가** | 마스크 IoU, 판정 정확도, 임계값 보정 | 저장한 프레임 + 정답 라벨 (`data/sim/`) | **파이프라인을 거치지 않고** 평가 스크립트가 C2·C3 함수를 직접 호출 | 과자 하나하나 (IoU, 혼동행렬) |
| **② 예비·성능** (선택) | 시연 예비 화면, Scale-out 전후 처리 fps | 시뮬레이터 **화면 녹화** 1~2분 (`data/videos/demo.mp4`) | `tools/fake_camera.py --video`로 **전체 파이프라인** | 시뮬레이터 카운터와 누적 집계 비교 |
| **③ 시연** | 실제 사용 모습 | **폰으로 모니터를 촬영** | QR 접속 → 전체 파이프라인 | 눈으로 확인 + 카운터 비교 |

**① 정확도 평가를 파이프라인으로 돌리지 않는 이유**
- C1은 받아들인 프레임에만 번호를 붙이고, 바쁘면 프레임을 버립니다. 결과가 어느 라벨 파일의 짝인지 알 수 없게 됩니다.
- 대시보드로 오는 결과에는 마스크 윤곽선이 빠져 있어서 IoU를 계산할 수 없습니다.
- 함수를 직접 부르면 Docker 없이 빠르고, 같은 입력에 늘 같은 결과가 나옵니다. 임계값을 바꿔 가며 여러 번 돌리기 좋습니다.
- 평가할 때는 C2의 리사이즈(`PREPROC_WIDTH`)를 실제 실행과 같게 맞춥니다. 저장된 PNG는 화면 배율 때문에 1800px처럼 클 수 있습니다.

**② 화면 녹화는 선택입니다.** 시간이 남으면 1~2분짜리 하나만 만듭니다. 용도는 줌 시연 중 폰 연결이 불안정할 때의 예비 화면,
그리고 Scale-out 전후 비교 입력 두 가지뿐입니다. 폰 촬영보다 화질이 일정해서 같은 조건으로 다시 돌릴 수 있습니다.

**③ 폰 촬영의 한계와 요령**
- 화질이 떨어집니다. 모아레(물결무늬), 화면 주사선, 반사, 비스듬한 각도, 자동 노출 변화가 생깁니다.
  화면 좌표와 라벨 좌표도 맞지 않아서 **정확도 채점은 할 수 없습니다.** 보여주기와 개수 비교 용도입니다.
- 폰은 거치대에 고정하고 화면과 **정면으로 평행하게** 둡니다. 모니터 밝기는 높이고, 실내 조명은 낮춰서 반사를 줄입니다.
- 시뮬레이터의 **정답 외곽선 표시는 끕니다.** 검사선(빨간 점선)은 끌 수 없어서 영상에 찍힙니다.
- 화면 전체가 아니라 컨베이어 영역만 꽉 차게 찍습니다. 그래야 C4 검사선(`COUNT_LINE_X`)이 시뮬레이터의 검사선과 맞습니다.
  어긋나면 `.env`의 `COUNT_LINE_X`를 조정합니다.

## 시뮬레이터 사용법

`tools/simulator/conveyor-cookie-sim.html`을 Chrome으로 엽니다 (파일을 더블클릭하면 됩니다. 서버는 필요 없습니다).
인터넷이 없으면 글꼴만 기본 글꼴로 바뀌고 동작은 같습니다.

### 화면과 설정

- 오른쪽 화면: 컨베이어 영상입니다. 빨간 점선이 **검사선**이고, 과자가 이 선을 지날 때 위쪽 카운터(검사 통과 / 정상 / 불량)가 1씩 올라갑니다.
- 왼쪽 패널

| 그룹 | 설정 | 범위 |
|---|---|---|
| 라인 구성 | 생산 라인 수 / 라인 간격 / 컨베이어 속도 | 1~6개 / 70~260px / 0~600px/s |
| 과자 규격 | 과자 지름 / 과자 간격 | 18~200px / 4~400px (모든 라인 같은 지름) |
| 불량 조건 | 배치 방식 | **혼합 랜덤**(불량률만큼 무작위) / **나란히 비교**(2·4·6번 라인만 불량) |
| | 불량률 / 결손 크기 | 0~100% / 10~100% (결손 크기가 클수록 많이 잘려 나감) |
| 촬영 환경 | 조명 불균일 / 센서 노이즈 / 그림자 | 0~100% / 0~100% / 켜기·끄기 |
| 정답 오버레이 | 정답 외곽선 표시 | 확인용 (정상 초록, 불량 빨강) |

- 라인 수, 라인 간격, 배치 방식, "라인별 위상"을 바꾸면 벨트가 비워지고 처음부터 다시 흐릅니다.

### 평가 데이터 만들기 ① 자동 캡처 (권장)

PocketFab에서 시뮬레이터에 추가한 기능입니다. 프레임과 라벨을 **같은 순간에** 만들어서 짝이 어긋날 일이 없습니다.

1. 원하는 설정으로 맞추고, 컨베이어가 과자로 찰 때까지 잠시 기다립니다.
2. 왼쪽 패널 **"자동 캡처"** 에서 **저장할 쌍 수**(1~200)와 **쌍 사이 간격**(1~90프레임, 1프레임 = 1/30초)을 정합니다.
3. **"자동 캡처 → ZIP"** 을 누릅니다. 진행 상황이 패널에 표시되고, 끝나면 `sim_<시각>.zip` 하나가 내려받아집니다.
4. ZIP 안의 `0001.png`, `0001.json`, … 을 그대로 `data/sim/<세션>/`에 풉니다. 이름 바꾸기가 필요 없습니다.

- 캡처하는 동안 시뮬레이터는 멈추고, 버튼과 단축키는 잠깁니다. 끝나면 원래 상태(재생/정지)로 돌아갑니다.
- 라벨 JSON에는 `sequence`(`index`, `count`, `step_frames`, `dt_s`)가 추가로 들어갑니다. 쌍 사이 시간 간격을 알 수 있어서 추적·집계 시험에도 쓸 수 있습니다.
- 간격을 짧게(예: 1~3프레임) 잡으면 연속 영상처럼 쓸 수 있고, 길게(예: 30프레임 이상) 잡으면 겹치는 과자가 적은 다양한 장면을 얻습니다.
- 한 번에 받는 용량은 대략 "쌍 수 × PNG 크기"입니다. 화면이 크고 배율이 2배면 장당 1~3MB이므로 100쌍 이상은 나눠 받는 편이 좋습니다.

### 평가 데이터 만들기 ② 손으로 저장 (S / L)

**프레임 저장(S)과 라벨 저장(L)은 서로 다른 파일로 따로 저장됩니다.** 재생 중에 누르면 두 키를 누르는 사이에 과자가 움직여서
이미지와 라벨이 어긋납니다. **반드시 일시정지한 상태에서 S와 L을 둘 다 누릅니다.**

1. **Space** (또는 "일시정지" 버튼) — 멈춤
2. **S** (또는 "프레임 저장") — `frame_<시각>.png` 다운로드
3. **L** (또는 "정답 라벨 JSON 내보내기") — `labels_<시각>.json` 다운로드
4. 두 파일을 `data/sim/<세션>/`으로 옮기고 **같은 번호로 이름을 바꿉니다** (`0001.png`, `0001.json`)
5. **Space** — 다시 재생하고, 장면이 바뀌면 1번부터 반복합니다

주의할 점
- **한 쌍씩 저장합니다** (S → L → 이름 변경). S S L L처럼 몰아서 누르면 어느 것이 짝인지 알 수 없습니다.
  파일 이름의 시각은 S와 L 사이에 1초가 바뀔 수 있어서 이름만으로 짝을 찾을 수 없습니다.
- **단축키가 안 먹으면 화면의 빈 곳을 한 번 클릭합니다.** 슬라이더나 버튼을 누른 직후에는 그쪽에 포커스가 남아 있어서 단축키가 무시됩니다.
- 저장되는 프레임에는 정답 외곽선과 검사선이 자동으로 빠집니다. 화면에만 보입니다.
- 이미지 크기는 "브라우저 창 폭 × 디스플레이 배율"로 정해집니다. 한 세션 안에서는 **창 크기와 브라우저 확대(100%)를 바꾸지 않습니다.**
- "한 프레임" 버튼은 멈춘 상태에서 1/30초만 진행합니다. 연속된 프레임 쌍을 만들 때 씁니다.
- 화면 가장자리에 걸친 과자도 라벨에 들어 있습니다. 잘려 보이는 과자는 평가에서 제외합니다 ([performance-plan](docs/performance-plan.md) 7장).

### 시연할 때

시연 설정(정답 외곽선 끄기, 속도 낮추기)과 순서는 [줌 시연 방법](#줌-시연-방법)을 따릅니다.
검사선(빨간 점선)은 끌 수 없어서 항상 화면에 보입니다.

## 평가 데이터 (각자 자동 캡처로 생성)

평가 데이터는 시뮬레이터가 만드는 합성 데이터라서 **공유 저장소를 쓰지 않습니다.** 필요한 사람이 시뮬레이터의
자동 캡처([시뮬레이터 사용법](#시뮬레이터-사용법))로 직접 만들어 씁니다. `data/`는 `.gitignore`로 git에서 제외됩니다.

### 폴더 구조

```
data/
├── sim/
│   ├── calib/          # 필수: 임계값 보정용 (자동 캡처 ZIP을 그대로 풂)
│   │   ├── 0001.png
│   │   ├── 0001.json   # 같은 번호가 한 쌍
│   │   └── ...
│   └── test/           # 필수: 정확도 확인용 (calib과 같은 설정으로 따로 캡처)
└── videos/
    └── demo.mp4        # 선택: 1~2분 화면 녹화 (줌 시연 예비, Scale-out 비교)
```

- **`calib`과 `test`는 반드시 따로 캡처합니다.** 같은 데이터로 보정하고 확인하면 정확도가 실제보다 좋게 나옵니다.
  만드는 법과 권장 설정(30쌍, 간격 30프레임)은 [performance-plan](docs/performance-plan.md) 2장에 있습니다.
- 시뮬레이터 설정(라인 수, 지름, 결손 크기 등)은 라벨 JSON의 `params`에 모두 기록되므로 따로 적어 둘 필요가 없습니다.
- 영상은 많이 모으지 않습니다. `demo.mp4` 하나(많아야 둘)면 됩니다.

### 팀원끼리 같은 데이터가 필요해지면 (GitHub Releases)

같은 평가 데이터로 숫자를 맞춰 봐야 할 때만, 자동 캡처 ZIP을 이 저장소의 GitHub Releases에 첨부해 공유합니다.
- 올리기: GitHub 저장소 → **Releases → Draft a new release** → 태그 `eval-data-v1` → ZIP 파일(`sim_calib.zip`, `sim_test.zip`)을 끌어다 놓고 게시합니다.
- 받기: 같은 Release 페이지에서 ZIP을 내려받아 `data/sim/calib/`, `data/sim/test/`에 풉니다.
- 파일 하나는 2GB까지 올릴 수 있습니다. 데이터를 다시 만들면 태그 번호를 올립니다(`eval-data-v2`).

### 정답 라벨 형식 (`labels_*.json`)

시뮬레이터가 내보내는 형식이며, 좌표는 **저장된 PNG의 픽셀 좌표**입니다. 자세한 분석은
[docs/performance-plan.md](docs/performance-plan.md) 7장에 있습니다.

```json
{
  "image": { "width": 1800, "height": 900, "dpr": 2 },
  "params": { "lines": 3, "size": 78, "strength": 55, "mode": "mix", "scan_line_x": 612.0, "...": "..." },
  "counts": { "total_passed": 41, "defect_passed": 12, "in_frame": 15 },
  "annotations": [
    { "id": 17, "line": 2, "category": "deformed", "defect_type": "bite", "defect_type_ko": "한쪽 파임",
      "roundness": 0.8123, "diameter_px": 156.0,
      "bbox": [x, y, w, h], "segmentation": [[x1, y1, x2, y2, "... 180점"]] }
  ]
}
```

## 알려진 제약

- **CPU 추론이라 30fps를 모두 처리하지는 못합니다.** 입력은 30fps로 받고, 처리할 수 없는 프레임은 C1에서 버려서
  지연이 쌓이지 않게 합니다. 실제 처리 fps는 성능 측정으로 확인합니다.
- **CPU 한 대에서는 C3 복제(Scale-out)의 효과가 제한적입니다.** 복제본들이 같은 코어를 나눠 쓰기 때문입니다.
  자세한 내용은 [docs/performance-plan.md](docs/performance-plan.md)를 참고하세요.
- 판정 지표는 **같은 크기의 원형 쿠키**(시뮬레이터)를 전제로 합니다. 현재 기본 임계값은 이상적 마스크로 정한 임시값입니다.
  시연 전에 자동 캡처 데이터로 다시 보정합니다 ([performance-plan](docs/performance-plan.md) 3장, [대상 과자](#대상-과자)).
- SAM 2는 CPU에서 프레임당 약 3초라, 시연할 때는 시뮬레이터 속도를 낮춰야 집계가 맞습니다 ([줌 시연 방법](#줌-시연-방법)).
- 한 과자는 여러 프레임에 반복해서 찍힙니다. 대시보드의 **"누적 집계"가 과자 단위**(검사선 통과 시 1회, 다수결 판정)이고,
  "현재 프레임"은 프레임 단위 참고값입니다. 과자 단위 집계는 C4가 추적 상태를 메모리에 들고 있으므로 **C4는 복제하지 않습니다**.
- 집계는 처리된 프레임 사이에 과자가 **과자 간격의 약 80%보다 덜** 움직여야 정확합니다. 시뮬레이터 기본 속도에서는 처리 fps 1 이상이면 됩니다
  ([api-spec 4.2](docs/api-spec.md)).
