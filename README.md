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
| **C4 Visualizer** | 판정 결과를 원본 프레임에 마킹해 C1로 전송 |
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
│   └── fake_camera.py   # 폰 대신 프레임을 보내는 가짜 카메라 (테스트·부하 측정)
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
docker compose up -d --scale c3-inference=4           # 실행 중에 복제본 수 변경
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
| `MAX_IN_FLIGHT` | `4` | 동시에 처리 중일 수 있는 프레임 수. C3 복제본 수와 비슷하게 맞춤 |
| `SEGMENTER` | `opencv` | `opencv` / `sam2`(구현 예정) |
| `SKIP_C3_IF_NO_BOXES` | `false` | C2 박스 검출 구현 후 `true`로 변경 |
| `SIMULATED_INFER_MS` | `0` | C3 추론 시간을 흉내 내는 지연. 로드밸런싱 시험용 |

전체 목록은 [docs/api-spec.md](docs/api-spec.md) 6장을 참고하세요.

### 7. GPU 환경으로 전환 (예정)

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
```

## 데이터 공유

테스트 영상과 이미지는 git에 올리지 않고 **Google Drive**로 공유합니다 (`data/`는 `.gitignore`로 제외).

- **공유 드라이브 링크**: `(추후 추가)`
- **관리 담당**: 이기태 (촬영·수집)

### 받는 방법

1. 위 드라이브에서 `data/` 폴더를 통째로 내려받습니다.
2. 레포 루트에 `data/`로 둡니다. 폴더 구조는 아래와 같아야 합니다.
3. 새 데이터를 올릴 때는 드라이브에 올린 뒤, 드라이브에 있는 `data/README.md` 목록도 함께 갱신합니다 (이 파일도 git이 아니라 드라이브에서 관리합니다).

### 폴더 구조

```
data/
├── README.md               # 데이터 목록 (파일명, 촬영일, 과자, 촬영 조건, 개수)
├── raw/                    # 원본 녹화 영상 (가공 전, 촬영 세션 단위)
│   └── 20261103_s01/
│       ├── video.mp4
│       └── session.json    # 촬영 조건 (아래 참고)
├── clips/                  # 평가·부하 테스트용으로 자른 짧은 클립 (5~30초)
│   ├── normal/             # 정상 과자만
│   ├── defect/
│   │   ├── broken/         # 깨짐 (이 빠짐, 쪼개짐)
│   │   └── deformed/       # 형태 변형 (찌그러짐, 늘어남)
│   ├── mixed/              # 정상·불량이 섞인 실제 시연 시나리오 → 성능 측정 기본 입력
│   └── empty/              # 과자 없는 컨베이어 (오검출·빈 프레임 처리 확인용)
└── frames/                 # 임계값 보정용 정지 프레임 (과자 1개씩 라벨이 확실한 것)
    ├── normal/
    └── defect/
        ├── broken/
        └── deformed/
```

- **`clips/`** 는 성능 측정용 부하 생성 스크립트의 입력이고(`mixed/`가 기본), 판정 정확도 평가에도 씁니다.
- **`frames/`** 는 원형도·solidity의 정상/불량 분포를 보고 `CIRCULARITY_THRESHOLD`, `SOLIDITY_THRESHOLD`를 정하는 데 씁니다.
- 대상 과자가 여러 종류가 되면 `normal/`, `defect/` 아래에 과자 이름으로 하위 폴더를 한 단계 더 둡니다.

### 파일 이름 규칙

`{촬영일}_{과자}_{라벨}_{번호}.{확장자}` — 영문 소문자, 단어는 `-`로 연결합니다.

```
20261103_choco-cookie_broken_001.mp4
20261103_choco-cookie_normal_012.jpg
```

라벨: `normal`, `broken`, `deformed`, `mixed`, `empty`

### 촬영 조건 기록 (`session.json`)

같은 과자라도 조명과 거리가 바뀌면 지표 값이 달라지므로, 촬영 세션마다 조건을 남깁니다.

```json
{
  "date": "2026-11-03",
  "snack": "choco-cookie",
  "device": "Galaxy S23",
  "resolution": "640x480",
  "fps": 30,
  "lighting": "LED 스탠드 1개, 위쪽 45도",
  "camera_height_cm": 30,
  "conveyor_speed": "중간",
  "notes": ""
}
```

## 알려진 제약

- **CPU 추론이라 30fps를 모두 처리하지는 못합니다.** 입력은 30fps로 받고, 처리할 수 없는 프레임은 C1에서 버려서
  지연이 쌓이지 않게 합니다. 실제 처리 fps는 성능 측정으로 확인합니다.
- **CPU 한 대에서는 C3 복제(Scale-out)의 효과가 제한적입니다.** 복제본들이 같은 코어를 나눠 쓰기 때문입니다.
  자세한 내용은 [docs/performance-plan.md](docs/performance-plan.md)를 참고하세요.
- 원형도 판정은 **둥근 과자**를 전제로 합니다. 대상 과자는 팀에서 결정 중이며, 정해지면 임계값을 테스트 데이터로 보정합니다.
