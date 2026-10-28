# PocketFab API 스펙 (v1 초안)

컨테이너 간 주고받는 데이터 형식을 정의한다. **이 문서가 팀의 인터페이스 계약**이므로,
필드를 바꾸려면 먼저 이 문서를 수정하고 관련 담당자와 합의한 뒤 코드를 바꾼다.

- 상태: 초안 (코드 작성 전)
- 버전 접두사: 모든 내부 API는 `/v1/...`

---

## 1. 전체 흐름

```
[스마트폰 브라우저]
   │  WSS  /ws/camera  (JPEG 바이너리)
   ▼
 Nginx :443 ──────────────▶ C1 Gateway :8000
                               │  ① POST /v1/frames  (원본 JPEG)
                               ▼
                            C2 Preprocessing :8000   ← 전처리 + 과자 박스 검출(OpenCV)
                               │  ② POST /v1/infer   (전처리 JPEG + 원본 JPEG + boxes)
                               ▼
                            Nginx :8080 (내부 전용, least_conn)
                               │
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
              C3 #1         C3 #2   ...   C3 #N      ← SAM 2(박스 프롬프트) + 원형도 판정
                 └─────────────┼─────────────┘
                               │  ③ POST /v1/render  (원본 JPEG + 판정 결과)
                               ▼
                            C4 Visualizer :8000      ← 불량 영역 마킹
                               │  ④ POST /v1/results (마킹된 JPEG + 요약)
                               ▼
                            C1 Gateway
                               │  WSS  /ws/dashboard (JSON)
                               ▼
                        [대시보드 브라우저]
```

### 1.1 호출 방식: "동기식 체인"

①~③은 **동기식**이다. 각 서비스는 자기 처리를 마치고 **다음 서비스 호출이 끝날 때까지**
응답을 보류한 뒤, 최종 상태(`processed` / `dropped`)를 응답으로 돌려준다.

```
C1 ──①──▶ C2 ──②──▶ Nginx ──▶ C3 ──③──▶ C4 ──④──▶ C1(/v1/results, 결과 수신)
C1 ◀──①응답── C2 ◀──②응답── C3 ◀──③응답── C4
```

이렇게 하는 이유:
- **Nginx `least_conn`이 제대로 동작한다.** C3에 연결이 열려 있는 시간이 곧 추론 시간이므로
  Nginx가 "지금 바쁜 복제본"을 정확히 알고 한가한 쪽으로 보낸다.
  (C3가 202로 즉시 응답하면 모든 복제본이 항상 한가해 보여 분산이 무의미해진다.)
- **백프레셔가 자연스럽다.** C1은 "C2에 보낸 뒤 아직 응답받지 못한 요청 수(in-flight)"만 세면 된다.
- **드롭 통계를 응답으로 수집**할 수 있다.

④(C4 → C1)는 결과 전달 경로로, ① 응답과 별개로 C1에 들어온다. C1은 비동기 서버이므로 교착은 없다.

---

## 2. 공통 규칙

### 2.1 전송 형식
- 컨테이너 간 요청은 `multipart/form-data`.
  - `meta`: JSON 문자열 (`Content-Type: application/json`)
  - 이미지 파트: JPEG 바이너리 (`Content-Type: image/jpeg`)
- 응답은 `application/json`.
- base64는 **대시보드 WebSocket에서만** 사용한다 (내부 구간은 바이너리 유지).

### 2.2 공통 `meta` 필드

모든 단계의 `meta`에는 아래 필드가 포함되고, 각 단계는 받은 `meta`를 **보존한 채 필드를 추가**해서 넘긴다.

```json
{
  "session_id": "b7f3c2e1",
  "frame_id": 1532,
  "orig_size": { "w": 640, "h": 480 },
  "timings": {
    "c1_recv": 1730102400123.4,
    "c2_start": 1730102400125.0,
    "c2_end":   1730102400131.8
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `session_id` | string | 카메라 WebSocket 연결 1개당 C1이 발급 |
| `frame_id` | int | 세션 내에서 단조 증가하는 번호. C1이 부여 |
| `orig_size` | object | 원본 프레임의 픽셀 크기 |
| `timings` | object | 단계별 타임스탬프 (epoch **밀리초**, `time.time() * 1000`). 모든 컨테이너가 같은 호스트 시계를 쓰므로 단계 간 차이로 지연시간을 계산한다 |

`timings` 키 목록: `c1_recv`, `c2_start`, `c2_end`, `c3_start`, `c3_infer_start`, `c3_infer_end`, `c3_end`, `c4_start`, `c4_end`, `c1_result_recv`.

### 2.3 좌표계
- **모든 좌표는 0~1로 정규화**한다. `x`는 가로 비율, `y`는 세로 비율이다.
- C2는 리사이즈할 때 **종횡비를 유지하고 크롭하지 않는다.** 따라서 전처리 이미지의 정규화 좌표는
  원본 이미지의 정규화 좌표와 같고, C4는 원본에 바로 그릴 수 있다.
- 박스: `[x1, y1, x2, y2]` (좌상단, 우하단)
- 폴리곤: `[[x, y], [x, y], ...]` (외곽선, 시계 방향 무관)

### 2.4 공통 응답 (①②③)

```json
{
  "frame_id": 1532,
  "status": "processed",
  "drop_reason": null,
  "stage": "c4"
}
```

| `status` | 의미 |
|---|---|
| `processed` | C4까지 정상 처리되어 결과가 C1으로 전송됨 |
| `dropped` | 중간에 버려짐. `stage`에 버린 단계, `drop_reason`에 이유 |

`drop_reason` 값: `c1_busy`(in-flight가 `MAX_IN_FLIGHT`에 도달), `c3_busy`(모든 C3 복제본이 바쁨), `timeout`, `decode_error`, `upstream_error`.

### 2.5 HTTP 상태 코드
| 코드 | 상황 |
|---|---|
| 200 | 처리 완료 또는 정상적인 드롭 (응답 본문의 `status`로 구분) |
| 400 | 잘못된 요청 (meta 누락, JPEG 디코딩 실패) |
| 503 | 해당 서비스가 바쁨 (C3 전용, 아래 4.3 참고) |
| 504 | 다음 단계 타임아웃 |

### 2.6 헬스체크
모든 서비스: `GET /healthz` → `200 {"status": "ok", "service": "c3-inference", "replica": "<hostname>"}`

---

## 3. 스마트폰 ↔ C1

### 3.1 카메라 스트림: `WSS /ws/camera`

- 접속 URL: `wss://<PUBLIC_HOST>/ws/camera` (Nginx가 TLS를 처리하고 C1로 프록시)
- **클라이언트 → 서버 (바이너리 메시지)**: JPEG 1장 = 메시지 1개
  - 권장: 640×480, JPEG 품질 0.7, `CAMERA_FPS`(기본 30)로 전송
- **클라이언트 → 서버 (텍스트 메시지, 선택)**: 제어용 JSON
  ```json
  { "type": "hello", "device": "Galaxy S23", "width": 640, "height": 480, "fps": 30 }
  ```
- **서버 → 클라이언트 (텍스트, 1초마다)**: 상태 피드백
  ```json
  { "type": "stats", "session_id": "b7f3c2e1", "sent": 300, "accepted": 120, "dropped": 180,
    "processed": 115, "processed_fps": 4 }
  ```

C1은 프레임을 받으면 `frame_id`와 `c1_recv`를 부여한다. **in-flight 수가 `MAX_IN_FLIGHT` 이상이면
C2로 보내지 않고 즉시 버린다** (`dropped`, `stage: "c1"`). 30fps 입력을 그대로 처리할 수 없는
CPU 환경에서 지연이 쌓이지 않게 하는 핵심 장치다.

`MAX_IN_FLIGHT`의 기본값은 C3 복제본 수에서 자동으로 정한다 (6장). 복제본 1개당 1장이 기본인 이유는 다음과 같다.
이보다 크게 잡으면 C3가 거절할(503) 프레임까지 C2 전처리를 거치게 되어 CPU를 낭비한다.
추론 200ms·복제본 2개 실험에서 1장이면 8.0fps(드롭은 모두 C1 입구), 2장이면 8.9fps(209장이 C3에서 거절)였다.

---

## 4. 내부 API

### ① C1 → C2: `POST http://c2-preprocessing:8000/v1/frames`

| 파트 | 내용 |
|---|---|
| `meta` | 공통 meta (`session_id`, `frame_id`, `orig_size`, `timings.c1_recv`) |
| `original` | 원본 JPEG |

응답: 공통 응답 (2.4). 타임아웃: `C1_PIPELINE_TIMEOUT_S` (기본 10초).

### ② C2 → C3 (Nginx 경유): `POST http://nginx:8080/v1/infer`

C2가 수행하는 작업: 리사이즈 → 노이즈 제거 → 밝기 보정 → **과자 후보 박스 검출(OpenCV)**

| 파트 | 내용 |
|---|---|
| `meta` | 공통 meta + 아래 필드 |
| `image` | 전처리된 JPEG (C3 추론 입력) |
| `original` | 원본 JPEG (C3는 쓰지 않고 C4로 그대로 전달) |

`meta` 추가 필드:
```json
{
  "proc_size": { "w": 640, "h": 480 },
  "preprocess": { "resize": 640, "denoise": "gaussian_5", "brightness": "clahe" },
  "boxes": [
    { "box_id": 0, "box": [0.12, 0.30, 0.31, 0.58], "area_ratio": 0.052 },
    { "box_id": 1, "box": [0.55, 0.28, 0.74, 0.60], "area_ratio": 0.061 }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `proc_size` | 전처리 이미지 크기 |
| `preprocess` | 적용한 전처리 기록 (디버깅·실험 기록용, 자유 형식) |
| `boxes` | 과자 후보 박스 목록. **반드시 포함하고, 없으면 빈 배열 `[]`** |

**`boxes`가 빈 배열이고 `SKIP_C3_IF_NO_BOXES=true`이면 C2는 C3를 호출하지 않고 바로 C4로 보낸다**
(아래 ③과 같은 형식, `inference: null`, `objects: []`). 컨베이어에 과자가 없는 프레임에서 SAM 2 추론을 통째로 생략하기 위해서다.
박스 검출을 구현하기 전(뼈대 단계)에는 `false`로 두어 모든 프레임이 C3를 거치게 한다.

응답: 공통 응답. Nginx가 503을 반환하면 C2는 해당 프레임을 `dropped`(`c3_busy`)로 응답한다 (재시도하지 않음).

### ③ C3 → C4: `POST http://c4-visualizer:8000/v1/render`

C3가 수행하는 작업: `boxes`를 SAM 2 박스 프롬프트로 사용해 마스크 추출 → 마스크 외곽선에서
면적·둘레·볼록껍질 면적 계산 → 원형도 `C = 4πA / P²`, solidity `S = A / A_hull` → 판정 (아래 4.1)

| 파트 | 내용 |
|---|---|
| `meta` | 공통 meta + 아래 필드 |
| `original` | ②에서 받은 원본 JPEG 그대로 |

`meta` 추가 필드:
```json
{
  "inference": {
    "replica": "c3-inference-2",
    "segmenter": "sam2",
    "model": "sam2.1_hiera_tiny",
    "device": "cpu",
    "circularity_threshold": 0.80,
    "solidity_threshold": 0.95
  },
  "objects": [
    {
      "box_id": 0,
      "box": [0.12, 0.30, 0.31, 0.58],
      "polygon": [[0.13, 0.31], [0.30, 0.32], [0.29, 0.57], [0.14, 0.56]],
      "area_px": 5320,
      "perimeter_px": 262.4,
      "circularity": 0.971,
      "solidity": 0.991,
      "mask_score": 0.94,
      "verdict": "normal",
      "defect_reasons": []
    },
    {
      "box_id": 1,
      "box": [0.55, 0.28, 0.74, 0.60],
      "polygon": [[0.56, 0.29], [0.73, 0.30], [0.70, 0.59]],
      "area_px": 4210,
      "perimeter_px": 301.7,
      "circularity": 0.581,
      "solidity": 0.872,
      "mask_score": 0.91,
      "verdict": "defect",
      "defect_reasons": ["circularity", "solidity"]
    }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `inference.replica` | 처리한 C3 컨테이너의 hostname (Docker에서는 컨테이너 ID 앞 12자리, 예: `ee8b29335cb5`). **로드밸런싱 분포 확인용** |
| `inference.segmenter` | `sam2` 또는 `opencv` (비교용 기준선 모드, 5.2 참고) |
| `objects[].polygon` | 마스크 외곽선을 `cv2.approxPolyDP`로 단순화한 정규화 좌표 (최대 64점) |
| `objects[].area_px`, `perimeter_px` | `proc_size` 기준 픽셀 단위 |
| `objects[].circularity` | `4π × area_px / perimeter_px²` (완전한 원 = 1.0). 전체적인 형태 변형에 민감 |
| `objects[].solidity` | `area_px / 볼록껍질(cv2.convexHull) 면적` (오목한 곳이 없으면 1.0). 깨짐·이 빠짐에 민감 |
| `objects[].mask_score` | SAM 2가 예측한 IoU 점수 (opencv 모드에서는 `null`) |
| `objects[].verdict` | `normal` / `defect` / `uncertain` (판정 규칙은 4.1) |
| `objects[].defect_reasons` | 임계값을 벗어난 지표 이름 목록: `"circularity"`, `"solidity"`. `normal`·`uncertain`이면 `[]` |

응답: C4의 공통 응답을 받아 그대로 C2에 돌려준다.

#### 4.1 판정 규칙

위에서부터 순서대로 적용한다.

1. `mask_score < MASK_SCORE_MIN` → `uncertain` (마스크를 믿을 수 없으므로 지표로 판정하지 않음. opencv 모드에서는 이 단계를 건너뜀)
2. `circularity < CIRCULARITY_THRESHOLD` **또는** `solidity < SOLIDITY_THRESHOLD` → `defect`
   (벗어난 지표를 모두 `defect_reasons`에 기록)
3. 그 외 → `normal`

`circularity`, `solidity`는 `uncertain`일 때도 계산해서 채운다 (임계값 보정용 데이터 수집).
두 임계값은 대상 과자가 정해진 뒤 테스트 데이터의 정상/불량 분포를 보고 보정한다.

### ④ C4 → C1: `POST http://c1-gateway:8000/v1/results`

| 파트 | 내용 |
|---|---|
| `meta` | ③의 meta 전체 + `timings.c4_start`, `timings.c4_end` + 아래 `summary` |
| `image` | 마킹된 JPEG (불량: 빨간 윤곽선 + 원형도 라벨, 정상: 초록 윤곽선, 판단불가: 노랑) |

```json
{
  "summary": { "total": 2, "normal": 1, "defect": 1, "uncertain": 0 }
}
```

응답: `200 {"ok": true}`

C1은 `frame_id`가 해당 세션에서 마지막으로 표시한 번호보다 작으면 **표시하지 않고 버린다**
(C3 복제본이 여러 개라 결과가 순서 없이 도착할 수 있음).

---

## 5. C1 ↔ 대시보드

### 5.1 결과 스트림: `WSS /ws/dashboard`

서버 → 클라이언트 (텍스트 JSON, 결과 1건당 1개):
```json
{
  "type": "result",
  "session_id": "b7f3c2e1",
  "frame_id": 1532,
  "image_b64": "/9j/4AAQSkZJRgABAQ...",
  "summary": { "total": 2, "normal": 1, "defect": 1, "uncertain": 0 },
  "objects": [ { "box_id": 1, "circularity": 0.581, "solidity": 0.872,
                 "verdict": "defect", "defect_reasons": ["circularity", "solidity"] } ],
  "latency_ms": { "total": 812.3, "c2": 6.8, "c3_infer": 731.0, "c4": 4.1 },
  "replica": "c3-inference-2"
}
```

서버 → 클라이언트 (1초마다):
```json
{ "type": "stats", "input_fps": 30, "processed_fps": 4, "drop_rate": 0.87,
  "in_flight": 2, "max_in_flight": 2, "c3_replicas": 2,
  "replicas": { "ee8b29335cb5": 2, "d570a57d09e3": 2 } }
```
`replicas`는 최근 1초간 복제본별 처리 fps다.

### 5.2 기타 C1 엔드포인트
| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/` | 대시보드 페이지 |
| GET | `/camera` | 스마트폰 카메라 페이지 |
| GET | `/qr` | `https://<PUBLIC_HOST>/camera`를 담은 QR 이미지 (PNG) |
| GET | `/v1/config` | 대시보드용 설정 (`camera_url`, `max_in_flight`, `max_in_flight_source`: `auto`/`env`, `c3_replicas`) |
| GET | `/v1/metrics` | 누적 통계 JSON (성능 측정 스크립트가 수집) |

---

## 6. 설정 (환경변수)

CPU/GPU 전환과 튜닝 값은 **코드에 하드코딩하지 않고 환경변수로만** 바꾼다. 기본값은 CPU 기준이다.

| 변수 | 서비스 | 기본값 | 설명 |
|---|---|---|---|
| `PUBLIC_HOST` | C1 | (필수) | 폰이 접속할 PC의 LAN IP. QR에 들어감 |
| `MAX_IN_FLIGHT` | C1 | (비움 = 자동) | 동시에 파이프라인에 들어갈 수 있는 프레임 수. 비우면 `C3 복제본 수 × IN_FLIGHT_PER_REPLICA`로 자동 계산하고, 값을 넣으면 그 값을 우선한다 |
| `IN_FLIGHT_PER_REPLICA` | C1 | `1` | 자동 계산 시 복제본 1개당 허용 프레임 수 |
| `C3_SERVICE_HOST` | C1 | `c3-inference` | 복제본 수를 셀 때 조회하는 DNS 이름. Docker DNS가 복제본 전체의 IP를 돌려주며, 5초마다 다시 센다 |
| `C1_PIPELINE_TIMEOUT_S` | C1 | `10` | ① 요청 타임아웃 |
| `PREPROC_WIDTH` | C2 | `640` | 리사이즈 가로 픽셀 (종횡비 유지) |
| `BOX_MIN_AREA_RATIO` | C2 | `0.005` | 이보다 작은 후보는 노이즈로 보고 제외 |
| `MAX_BOXES` | C2 | `16` | 프레임당 최대 박스 수 |
| `SKIP_C3_IF_NO_BOXES` | C2 | `false` | 박스가 없으면 C3를 건너뜀. 박스 검출 구현 후 `true`로 |
| `DEVICE` | C3 | `cpu` | `cpu` / `cuda` |
| `SEGMENTER` | C3 | `sam2` | `sam2` / `opencv` (SAM 2 없이 C2 박스 영역의 OpenCV 윤곽선만 사용) |
| `SAM2_MODEL` | C3 | `sam2.1_hiera_tiny` | `sam2.1_hiera_tiny` / `sam2.1_hiera_small` (`scripts/download_checkpoints.py`로 받을 수 있는 것) |
| `SAM2_CHECKPOINT_DIR` | C3 | `/checkpoints` | 체크포인트 폴더. 레포의 `checkpoints/`를 이 경로로 볼륨 마운트한다 |
| `TORCH_NUM_THREADS` | C3 | `4` | 복제본 1개가 쓰는 CPU 스레드 수 (7장 참고) |
| `CIRCULARITY_THRESHOLD` | C3 | `0.80` | 원형도가 이 값 미만이면 `defect`. 테스트 데이터로 보정 필요 |
| `SOLIDITY_THRESHOLD` | C3 | `0.95` | solidity가 이 값 미만이면 `defect`. 테스트 데이터로 보정 필요 |
| `MASK_SCORE_MIN` | C3 | `0.5` | 이 값 미만이면 `uncertain` (sam2 모드만 해당) |
| `SIMULATED_INFER_MS` | C3 | `0` | 추론 시간을 흉내 내는 지연(ms). CPU는 쓰지 않으므로 로드밸런싱·백프레셔 시험 전용 |

GPU 전환 계획: C3 Dockerfile에 빌드 인자 `TORCH_VARIANT=cpu|cu124`를 두고,
`docker-compose.gpu.yml` 오버라이드에서 `DEVICE=cuda`와 GPU 예약(`deploy.resources.reservations.devices`)을
설정한다. 그러면 `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up`으로 전환된다.

---

## 7. 설계 검토 메모

### 7.1 박스 검출 → SAM 2 박스 프롬프트 (채택)

**의견: 가능하고 CPU 환경에서는 사실상 필수다. 검출은 C2에서 한다.**

- SAM 2 자동 분할(`SAM2AutomaticMaskGenerator`)은 기본적으로 32×32 = 1,024개의 점 프롬프트로
  마스크 디코더를 돌리고 후처리(NMS 등)까지 한다. CPU에서는 프레임당 수 초~수십 초가 걸릴 수 있다.
- 박스 프롬프트(`SAM2ImagePredictor`)를 쓰면 이미지 인코더를 1회 실행(`set_image`)한 뒤
  박스 K개를 **한 번의 배치**로 디코딩한다. 디코더 비용은 과자 수(K)에만 비례한다.
- 컨베이어 배경은 단순하고 고정돼 있으므로 OpenCV(그레이스케일 → 임계값/배경 차분 → 모폴로지 →
  `findContours` → `boundingRect`)만으로 박스를 충분히 찾을 수 있다.
- **C2에 두는 이유**
  1. 박스 검출은 가벼운 연산이라 무거운 C3 복제본의 CPU를 아낄 수 있다.
  2. 박스가 0개면 C3 호출 자체를 건너뛴다 (②의 빈 `boxes` 규칙).
  3. C3는 "주어진 박스 → 마스크 → 판정"에만 집중하므로 단위 테스트가 쉽다.
- **주의**: 박스 프롬프트를 써도 **이미지 인코더(Hiera)의 비용은 프레임마다 그대로 남고, CPU에서는 이것이 지배적인 비용이다.**
  자동 분할 대비 크게 빨라지는 것은 맞지만, 실시간 30fps는 기대하기 어렵다. 실제 수치는 성능 측정으로 확인한다.
- 박스 검출이 실패하는 경우(과자끼리 붙음, 그림자)를 대비해 C2는 박스를 약간 넓혀서(패딩 5~10%) 보낸다.
  붙어 있는 과자 분리는 테스트 데이터를 본 뒤 결정한다.

### 7.2 `SEGMENTER=opencv` 기준선 모드 (채택)

C2 박스 안에서 OpenCV 윤곽선(임계값 → 모폴로지 → 가장 큰 외곽선)만으로 마스크를 대신하는 모드를 C3에 함께 둔다.
원형도·solidity 계산, 판정 규칙(4.1), 출력 형식(③)은 sam2 모드와 똑같다. 다른 점은 `mask_score: null`,
`inference.segmenter: "opencv"`, `inference.model: null`이고, torch와 SAM 2를 불러오지 않는다는 것이다.
- **성능 기준선**: SAM 2가 추가하는 지연시간을 측정한다.
- **정확도 비교**: SAM 2 마스크가 OpenCV 윤곽선보다 판정을 얼마나 개선하는지 보여준다 (발표에서 SAM 2를 쓰는 근거).
- **시연 안전장치**: 시연 PC가 너무 느리면 이 모드로 전환한다.

### 7.3 판정 지표에 대한 메모
- 원형도는 **둥근 과자**를 전제로 한다. 대상 과자는 팀에서 결정 중이며, 정해지면 정상 과자의 원형도·solidity 분포를 측정해 임계값을 정한다.
- 깨짐(이 빠진 형태)은 원형도를 크게 떨어뜨리지 않을 수 있다. 그래서 **solidity**(면적 / 볼록껍질 면적)를
  보조 지표로 함께 쓰고, **두 지표 중 하나라도 임계값을 벗어나면 불량**으로 판정한다 (4.1).
  - 원형도: 찌그러짐, 길쭉해짐 같은 전체 형태 변형에 민감
  - solidity: 가장자리가 오목하게 파인 깨짐에 민감. 단, 반으로 쪼개진 조각은 볼록하므로 solidity가 높게 나와서 원형도가 잡아야 한다
- 저해상도에서는 픽셀 계단 때문에 둘레가 과대 계산되어 원형도가 낮게 나온다.
  `proc_size`를 바꾸면 임계값도 다시 보정해야 한다.

### 7.4 CPU 환경에서의 Scale-out 한계
`docs/performance-plan.md` 참고. 요약하면, 복제본들이 같은 CPU 코어를 나눠 쓰므로
**C3 복제본 수 × `TORCH_NUM_THREADS`를 물리 코어 수(개발 PC 기준 8) 안으로 맞춰야** 하고,
처리량 증가는 선형이 아니라 금방 포화된다.
