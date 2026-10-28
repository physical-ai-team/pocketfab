# PocketFab — 주머니 속의 스마트 팩토리

스마트폰 카메라 하나로 컨베이어 위 과자의 불량(깨짐, 형태 변형)을 **실시간 영상(30fps 입력)** 에서 검출하는 시스템입니다.
SAM 2로 과자 마스크를 추출하고, OpenCV로 원형도를 계산해 정상/불량을 판정한 뒤, 결과를 웹 대시보드에 표시합니다.

> 현재 상태: **뼈대 파이프라인 완성**. 폰 카메라 → C1 → C2 → C3 → C4 → 대시보드까지 프레임이 흐릅니다.
> C2 전처리·박스 검출, C3 SAM 2 추론, C4 윤곽선 그리기는 각 담당자가 채울 차례입니다 (각 폴더의 README 참고).

## 주요 특징

- **설치 없는 카메라 연결**: QR코드를 찍으면 스마트폰 브라우저가 바로 카메라 스트리밍을 시작합니다.
- **SAM 2 기반 정밀 분할**: OpenCV로 과자 위치(박스)를 먼저 찾고, 그 박스를 SAM 2 프롬프트로 넘겨 마스크를 얻습니다.
- **형태 지표 판정**: 두 지표 중 **하나라도** 임계값을 벗어나면 불량으로 판정합니다.
  - 원형도 `Circularity = 4π × Area / Perimeter²` (완전한 원 = 1.0): 찌그러짐 같은 형태 변형을 잡습니다.
  - Solidity `Area / 볼록껍질 면적` (오목한 곳이 없으면 1.0): 가장자리가 파인 깨짐을 잡습니다.
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
| `bite` 한쪽 파임 | 가장자리가 원 모양으로 물어뜯김 | 오목 | **solidity** (원형도는 정상과 차이가 작음) |
| `cut` 단면 절단 | 가장자리가 직선으로 잘림 | 볼록 | **둘 다 약함** — solidity가 정상과 같고 원형도 차이도 작음 |
| `wedge` 조각 결손 | 중심까지 부채꼴로 빠짐 | 오목 | **원형도 + solidity** 둘 다 크게 떨어짐 |
| `half` 반쪽 파손 | 반원 | 볼록 | **원형도** (solidity는 정상과 같음) |

> ⚠️ 이상적인 마스크로 측정해 보면, **현재 기본 임계값(원형도 0.80, solidity 0.95)으로는 `bite`의 절반 이상과 `cut` 전부를 놓칩니다.**
> 정상 쿠키도 픽셀 외곽 때문에 원형도가 1.0이 아니라 약 0.90으로 나옵니다. 임계값 재보정과 `cut`용 보조 지표가 필요합니다.
> 측정값은 [docs/api-spec.md](docs/api-spec.md) 7.3에 있고, `python tools/simulator/shape_metrics.py`로 다시 잴 수 있습니다.

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
│   ├── fake_camera.py   # 폰 대신 프레임을 보내는 가짜 카메라 (테스트·부하 측정)
│   └── simulator/       # 대상 과자 시뮬레이터(HTML), 형상 지표 분석 스크립트
├── checkpoints/         # SAM 2 체크포인트 (git 제외, 스크립트로 받음)
├── data/                # 테스트 영상·이미지 (git 제외, Google Drive로 공유)
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
| `SEGMENTER` | `opencv` | `opencv` / `sam2`(구현 예정) |
| `SKIP_C3_IF_NO_BOXES` | `false` | C2 박스 검출 구현 후 `true`로 변경 |
| `SIMULATED_INFER_MS` | `0` | C3 추론 시간을 흉내 내는 지연. 로드밸런싱 시험용 |

전체 목록은 [docs/api-spec.md](docs/api-spec.md) 6장을 참고하세요.

### 7. GPU 환경으로 전환 (예정)

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

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

### 프레임과 정답 라벨 저장 (평가 데이터 만들기)

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

- **정답 외곽선 표시는 끕니다.** 화면에 그려진 외곽선이 폰 카메라에 그대로 찍혀서 검출에 섞입니다.
- 검사선(빨간 점선)은 끌 수 없어서 항상 화면에 보입니다.

## 데이터 공유

시뮬레이터로 만든 평가 데이터와 녹화 영상은 git에 올리지 않고 **Google Drive**로 공유합니다 (`data/`는 `.gitignore`로 제외).

- **공유 드라이브 링크**: `(추후 추가)`
- **관리 담당**: 이기태 (촬영·수집)

### 받는 방법

1. 위 드라이브에서 `data/` 폴더를 통째로 내려받습니다.
2. 레포 루트에 `data/`로 둡니다. 폴더 구조는 아래와 같아야 합니다.
3. 새 데이터를 올릴 때는 드라이브에 올린 뒤, 드라이브에 있는 `data/README.md` 목록도 함께 갱신합니다 (이 파일도 git이 아니라 드라이브에서 관리합니다).

### 폴더 구조

```
data/
├── README.md                          # 세션 목록: 설정 요약, 프레임 수, 용도(calib / test)
├── sim/                               # 시뮬레이터 캡처: 프레임 + 정답 라벨 쌍 (정확도 평가·임계값 보정)
│   ├── 20261103_mix-d78-s55-l20-n0/   # 세션 = 시뮬레이터 설정 하나
│   │   ├── 0001.png                   # 프레임 (S)
│   │   ├── 0001.json                  # 정답 라벨 (L) — 같은 번호가 한 쌍
│   │   ├── 0002.png
│   │   └── 0002.json
│   └── 20261103_pair-d78-s25-l60-n30/
└── videos/                            # 연속 영상 (부하 측정, 중복 집계 시험, 시연 리허설)
    ├── 20261103_mix-d78-s55-l20-n0_screen.mp4   # 화면 녹화 (OBS 등)
    └── 20261103_mix-d78-s55-l20-n0_phone.mp4    # 폰으로 모니터를 촬영한 영상
```

- **`sim/`** 은 정답이 있어서 마스크 IoU 측정과 임계값 보정에 씁니다. 세션은 **보정용(calib)과 검증용(test)으로 나누고**,
  어느 쪽인지 `data/README.md`에 적습니다. 같은 세션의 프레임을 양쪽에 섞지 않습니다.
- **`videos/`** 는 정답 라벨이 없습니다. 처리 fps·지연 측정(`tools/fake_camera.py --video`)과 시연 리허설에 씁니다.
  시뮬레이터 화면의 카운터(검사 통과 / 불량)를 녹화 끝에 메모해 두면, 집계 결과를 대략 비교할 수 있습니다.

### 이름 규칙

- **세션 폴더**: `{날짜}_{배치}-d{지름}-s{결손 크기}-l{조명 불균일}-n{노이즈}` — 설정값은 슬라이더 숫자 그대로 씁니다.
  - `배치`: `mix`(혼합 랜덤) / `pair`(나란히 비교)
  - 예: `20261103_mix-d78-s55-l20-n0`
  - 그 밖의 설정(라인 수, 속도, 불량률 등)은 라벨 JSON의 `params`에 모두 기록되므로 폴더 이름에 넣지 않습니다.
- **프레임 쌍**: `NNNN.png` + `NNNN.json` (4자리 번호, 세션마다 0001부터). **번호가 같은 파일이 한 쌍입니다.**
- **영상**: `{세션 이름}_{screen|phone}.mp4`

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

### 폰 촬영 영상의 조건 기록

폰으로 모니터를 찍은 영상은 같은 설정이라도 거리·각도·화면 밝기에 따라 결과가 달라집니다.
`data/README.md`의 해당 영상 항목에 기기, 모니터와의 거리, 각도, 화면 밝기, 실내 조명을 한 줄로 적습니다.

## 알려진 제약

- **CPU 추론이라 30fps를 모두 처리하지는 못합니다.** 입력은 30fps로 받고, 처리할 수 없는 프레임은 C1에서 버려서
  지연이 쌓이지 않게 합니다. 실제 처리 fps는 성능 측정으로 확인합니다.
- **CPU 한 대에서는 C3 복제(Scale-out)의 효과가 제한적입니다.** 복제본들이 같은 코어를 나눠 쓰기 때문입니다.
  자세한 내용은 [docs/performance-plan.md](docs/performance-plan.md)를 참고하세요.
- 판정 지표는 **같은 크기의 원형 쿠키**(시뮬레이터)를 전제로 합니다. 현재 기본 임계값은 임시값이라 `bite`·`cut` 불량을 놓칩니다.
  시뮬레이터 라벨로 다시 보정해야 합니다 ([대상 과자](#대상-과자)).
- 한 과자는 여러 프레임에 반복해서 찍힙니다. 대시보드의 **"누적 집계"가 과자 단위**(검사선 통과 시 1회, 다수결 판정)이고,
  "현재 프레임"은 프레임 단위 참고값입니다. 과자 단위 집계는 C4가 추적 상태를 메모리에 들고 있으므로 **C4는 복제하지 않습니다**.
- 집계는 처리된 프레임 사이에 과자가 **과자 간격의 약 80%보다 덜** 움직여야 정확합니다. 시뮬레이터 기본 속도에서는 처리 fps 1 이상이면 됩니다
  ([api-spec 4.2](docs/api-spec.md)).
