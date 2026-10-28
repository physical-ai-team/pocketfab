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
면적·둘레·볼록껍질·최소 외접원 계산 → 원형도 `C = 4πA / P²`, solidity `S = A / A_hull`,
원 채움률 `F = A / (π·r_enc²)`, 오목 깊이 `D = 최대 오목 깊이 / r_enc` → 판정 (아래 4.1)

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
    "solidity_threshold": 0.95,
    "fill_ratio_threshold": 0.93,
    "concavity_threshold": 0.06
  },
  "objects": [
    {
      "box_id": 0,
      "box": [0.12, 0.30, 0.31, 0.58],
      "polygon": [[0.13, 0.31], [0.30, 0.32], [0.29, 0.57], [0.14, 0.56]],
      "area_px": 5320,
      "perimeter_px": 262.4,
      "circularity": 0.903,
      "solidity": 0.989,
      "fill_ratio": 0.970,
      "concavity": 0.023,
      "mask_score": 0.94,
      "verdict": "normal",
      "defect_reasons": [],
      "defect_type": null
    },
    {
      "box_id": 1,
      "box": [0.55, 0.28, 0.74, 0.60],
      "polygon": [[0.56, 0.29], [0.73, 0.30], [0.70, 0.59]],
      "area_px": 4210,
      "perimeter_px": 301.7,
      "circularity": 0.581,
      "solidity": 0.872,
      "fill_ratio": 0.840,
      "concavity": 0.801,
      "mask_score": 0.91,
      "verdict": "defect",
      "defect_reasons": ["circularity", "solidity", "fill_ratio", "concavity"],
      "defect_type": "wedge"
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
| `objects[].fill_ratio` | 원 채움률 `area_px / 최소 외접원(cv2.minEnclosingCircle) 면적` (완전한 원 = 1.0). 볼록하게 잘린 `cut`·`half`에 민감 |
| `objects[].concavity` | 오목 깊이 `최대 오목 깊이(cv2.convexityDefects) / 외접원 반지름` (오목한 곳이 없으면 0). 가장자리가 파인 `bite`·`wedge`에 민감 |
| `objects[].mask_score` | SAM 2가 예측한 IoU 점수 (opencv 모드에서는 `null`) |
| `objects[].verdict` | `normal` / `defect` / `uncertain` (판정 규칙은 4.1) |
| `objects[].defect_reasons` | 임계값을 벗어난 지표 이름 목록: `"circularity"`, `"solidity"`, `"fill_ratio"`, `"concavity"`. `normal`·`uncertain`이면 `[]` |
| `objects[].defect_type` | 추정 불량 유형 `bite` / `cut` / `wedge` / `half` (4.1). `normal`·`uncertain`이면 `null` |

응답: C4의 공통 응답을 받아 그대로 C2에 돌려준다.

#### 4.1 판정 규칙

위에서부터 순서대로 적용한다.

1. `mask_score < MASK_SCORE_MIN` → `uncertain` (마스크를 믿을 수 없으므로 지표로 판정하지 않음. opencv 모드에서는 이 단계를 건너뜀)
2. `circularity < CIRCULARITY_THRESHOLD` **또는** `solidity < SOLIDITY_THRESHOLD` **또는**
   `fill_ratio < FILL_RATIO_THRESHOLD` **또는** `concavity > CONCAVITY_THRESHOLD` → `defect` (벗어난 지표를 모두 `defect_reasons`에 기록)
3. 그 외 → `normal`

`defect`이면 `defect_type`을 추정한다. 오목하게 파였으면(`concavity > CONCAVITY_THRESHOLD`) `concavity > 0.45`일 때 `wedge`,
아니면 `bite`. 볼록하게 잘렸으면 `fill_ratio < 0.70`일 때 `half`, 아니면 `cut` (경계값 근거는 7.3).

네 지표는 `uncertain`일 때도 계산해서 채운다 (임계값 보정용 데이터 수집).
현재 기본값은 이상적인 마스크로 정한 임시값이며(7.3), 시뮬레이터 라벨로 보정한다 (performance-plan 3장).

### ④ C4 → C1: `POST http://c1-gateway:8000/v1/results`

| 파트 | 내용 |
|---|---|
| `meta` | ③의 meta 전체 + `timings.c4_start`, `timings.c4_end` + 아래 `summary`, `tracking` + `objects[]`에 `track_id`, `truncated` |
| `image` | 마킹된 JPEG (불량: 빨간 윤곽선 + 원형도 라벨, 정상: 초록 윤곽선, 판단불가: 노랑, 검사선, 누적 집계) |

```json
{
  "summary": { "total": 2, "normal": 1, "defect": 1, "uncertain": 0 },
  "tracking": {
    "count_line_x": 0.34,
    "direction": "left",
    "used_for_tracking": true,
    "counted": [ { "track_id": 17, "verdict": "defect", "votes": { "normal": 1, "defect": 4, "uncertain": 0 },
                   "defect_type": "bite" } ],
    "totals": { "total": 41, "normal": 29, "defect": 12, "uncertain": 0 },
    "active_tracks": 9
  }
}
```

| 필드 | 설명 |
|---|---|
| `summary` | **이 프레임에** 보이는 과자의 판정 개수 (프레임 단위) |
| `tracking.counted` | **이 프레임에서** 검사선을 넘은 과자. `verdict`는 다수결 결과, `votes`는 그 과자가 받은 프레임별 판정 수, `defect_type`은 불량 판정 프레임들에서 가장 많이 나온 유형 (`verdict`가 `defect`가 아니면 `null`) |
| `tracking.totals` | 세션 시작 이후 **과자 단위** 누적 집계. 대시보드의 "누적 집계"와 시연 결과는 이 값을 쓴다 |
| `tracking.used_for_tracking` | `false`면 늦게 도착한 프레임이라 추적에 쓰지 않았다는 뜻 (그림만 그림) |
| `objects[].track_id` | 추적 번호. 화면 끝에 걸친 과자는 `null` |
| `objects[].truncated` | 박스가 화면 끝(`EDGE_MARGIN` 이내)에 닿은 과자. 판정은 그대로 오지만 집계·다수결에는 쓰지 않는다 |

응답: `200 {"ok": true}`

C1은 `frame_id`가 해당 세션에서 마지막으로 표시한 번호보다 작으면 **표시하지 않고 버린다**
(C3 복제본이 여러 개라 결과가 순서 없이 도착할 수 있음).

#### 4.2 과자 단위 집계 (C4)

한 과자는 화면을 지나는 동안 여러 프레임에 찍힌다. 프레임마다 세면 중복이 생기므로 C4가 과자를 추적해서 한 번만 센다.
구현은 `c4-visualizer/app/tracker.py`.

1. **추적**: 세션마다 따로 한다. 잘리지 않은(`truncated=false`) 과자를 기존 추적과 이어 붙인다. 조건은 세 가지다.
   - 진행 방향(`COUNT_DIRECTION`)으로 움직였다 (반대 방향은 0.01까지만 허용)
   - 같은 라인이다 (세로 중심 차이 < 박스 높이의 절반)
   - 속도로 예측한 위치에 가장 가깝다 (안 보이던 동안에도 계속 움직였다고 본다)

   이어지지 않은 과자는 새 추적을 시작한다. `TRACK_MAX_AGE_S` 동안 안 보이거나 예측 위치가 화면 밖으로 나간 추적은 끝낸다.
2. **집계**: 추적 중인 과자의 중심이 검사선(`COUNT_LINE_X`)을 넘는 순간 **한 번만** 센다.
3. **판정**: 그때까지 그 과자가 받은 프레임별 판정의 **다수결**로 정한다. `uncertain`은 표에 넣지 않는다.
   정상과 불량 표가 같으면 **불량**으로 본다(검사에서는 불량을 놓치는 쪽이 더 위험하다). 표가 하나도 없으면 `uncertain`이다.
4. **늦게 온 프레임**: C3 복제본 때문에 프레임 순서가 뒤바뀌어 도착한다. 이미 처리한 `frame_id`보다 작은 프레임은 추적에 쓰지 않는다.

**제약 — C4는 복제하지 않는다.** 추적 상태가 C4 프로세스의 메모리에 있다. C4를 여러 개로 늘리면 같은 과자의 프레임이
서로 다른 C4로 흩어져 중복·누락이 생긴다. Scale-out은 C3만 한다.

**동작 조건**: 처리된 프레임 사이에 과자가 움직이는 거리가 **과자 간격(지름 + 간격)의 약 80%보다 작아야** 한다.
시뮬레이터 기본값(속도 130px/s, 지름 78 + 간격 90 = 168px)에서 시험한 결과는 다음과 같다(30초, 3개 라인, `c4-visualizer/tests`).
처리 fps 1~3에서는 정답과 같은 수를 셌고(이동 43~130px), 0.8fps에서는 무너졌다(이동 162px).
CPU에서 처리 fps가 1보다 낮으면 시뮬레이터 속도를 낮춘다.

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
  "objects": [ { "box_id": 1, "circularity": 0.581, "solidity": 0.872, "fill_ratio": 0.840, "concavity": 0.801,
                 "verdict": "defect", "defect_reasons": ["circularity", "solidity", "fill_ratio", "concavity"],
                 "defect_type": "wedge", "track_id": 17, "truncated": false } ],
  "latency_ms": { "total": 812.3, "c2": 6.8, "c3_infer": 731.0, "c4": 4.1 },
  "replica": "c3-inference-2",
  "tracking": { "totals": { "total": 41, "normal": 29, "defect": 12, "uncertain": 0 }, "counted": [], "...": "④와 같음" }
}
```

서버 → 클라이언트 (1초마다):
```json
{ "type": "stats", "input_fps": 30, "processed_fps": 4, "drop_rate": 0.87,
  "in_flight": 2, "max_in_flight": 2, "c3_replicas": 2,
  "replicas": { "ee8b29335cb5": 2, "d570a57d09e3": 2 } }
```
`replicas`는 최근 1초간 복제본별 처리 fps다.

`result`의 `objects[]`는 ④의 `objects[]`에서 대시보드에 필요한 필드만 옮긴 것이다
(`box`, `polygon`, `area_px` 등 좌표·픽셀 값은 빠진다. 윤곽선은 이미 `image_b64`에 그려져 있다).
대시보드는 `tracking.counted`의 불량 과자를 "최근 불량" 목록에 썸네일과 함께 남긴다. 불량 유형은 `counted[].defect_type`(C4 다수결)을 쓴다.
검사선을 넘은 그 프레임 하나의 판정은 정상일 수 있어서, `objects[]`에서 유형을 찾으면 "unknown"이 나오기 때문이다.

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
| `FILL_RATIO_THRESHOLD` | C3 | `0.93` | 원 채움률이 이 값 미만이면 `defect`. 시연 해상도(과자 지름 약 40~50px)에 맞춰 보정함 (7.3) |
| `CONCAVITY_THRESHOLD` | C3 | `0.06` | 오목 깊이가 이 값을 넘으면 `defect` (`bite`·`wedge`). 정상 0.015~0.035, 결손 10% `bite` 0.08 이상 |
| `MASK_SCORE_MIN` | C3 | `0.5` | 이 값 미만이면 `uncertain` (sam2 모드만 해당) |
| `SIMULATED_INFER_MS` | C3 | `0` | 추론 시간을 흉내 내는 지연(ms). CPU는 쓰지 않으므로 로드밸런싱·백프레셔 시험 전용 |
| `COUNT_LINE_X` | C4 | `0.34` | 검사선 x 위치 (화면 폭 비율). 시뮬레이터의 검사선과 같다 |
| `COUNT_DIRECTION` | C4 | `left` | 과자 이동 방향 `left` / `right` |
| `TRACK_MAX_SPEED` | C4 | `1.0` | 과자의 최대 속도 (화면 폭/초). 이보다 빨리 움직인 것은 같은 과자로 보지 않는다 |
| `TRACK_MAX_AGE_S` | C4 | `15` | 이 시간 동안 안 보이면 추적을 끝낸다. 결과가 오는 간격보다 길어야 한다 (SAM 2 CPU 복제본 2개면 4~5초). 2초였을 때 SAM 2 시연에서 누적 집계가 0으로 남았다 |
| `EDGE_MARGIN` | C4 | `0.01` | 박스가 화면 끝에서 이 비율 안에 닿으면 잘린 과자(`truncated`)로 본다 |

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

대상 과자는 시뮬레이터(`tools/simulator/conveyor-cookie-sim.html`)의 **같은 크기 원형 초코칩 쿠키**로 확정됐다 (2026-10-28).
불량은 `bite`, `cut`, `wedge`, `half` 4가지이고, 모두 원에서 한 덩어리를 잘라낸 형태다.

**네 지표 중 하나라도 임계값을 벗어나면 불량**으로 판정한다 (4.1).
- 원형도: 외곽 전체의 형태 변화에 반응한다 (`half`, `wedge`)
- solidity: 오목하게 파인 곳에 반응한다 (`bite`, `wedge`). 볼록하게 잘린 `cut`, `half`는 정상과 구별하지 못한다
- 원 채움률: 외접원에서 빠진 면적에 반응한다 (`cut`, `half`, 큰 `bite`). 볼록·오목과 관계없이 동작하고, 크기와 무관한 비율이라
  폰 촬영 거리가 바뀌어도 그대로 쓸 수 있다
- 오목 깊이: 가장 깊게 파인 곳의 깊이에 반응한다 (`bite`, `wedge`). 면적 차이가 작은 작은 `bite`도 정상(약 0.02)과 크게 벌어진다

#### 이상적인 마스크에서 잰 지표 범위

`python tools/simulator/shape_metrics.py`로 측정했다. 시뮬레이터 `makeShape()`를 그대로 옮겨 유형마다 200개씩 만들고,
C3와 같은 `judge.measure()`로 쟀다. 지름 78px 기준이며, 156px에서는 원형도가 최대 0.015 낮게 나온다.
마스크 오차가 없는 "최선의 경우"이므로 실제 SAM 2 마스크에서는 범위가 더 넓어진다.

| 유형 | 결손 크기 | 원형도 | solidity | 원 채움률 | 불량 판정률 (기본값) |
|---|---|---|---|---|---|
| 정상 | — | 0.903 | 0.989 | 0.970 | 0% (오경보 없음) |
| `bite` | 25% / 55% / 85% | 0.85~0.87 / 0.80~0.85 / 0.74~0.83 | 0.96~0.98 / 0.94~0.96 / 0.90~0.95 | 0.94~0.95 / 0.89~0.93 / 0.82~0.91 | 100% / 100% / 100% |
| `cut` | 25% / 55% / 85% | 0.86~0.90 / 0.83~0.88 / 0.78~0.86 | 0.98~0.99 (모든 크기) | 0.89~0.92 / 0.82~0.88 / 0.74~0.84 | 100% / 100% / 100% |
| `wedge` | 25% / 55% / 85% | 0.57~0.62 (모든 크기) | 0.88~0.91 / 0.85~0.89 / 0.83~0.87 | 0.86~0.89 / 0.81~0.86 / 0.77~0.83 | 100% / 100% / 100% |
| `half` | 25% / 55% / 85% | 0.68~0.76 (모든 크기) | 0.97~0.99 (모든 크기) | 0.52~0.59 (모든 크기) | 100% / 100% / 100% |

불량 판정률은 기본값(원형도 < 0.80, solidity < 0.95, 원 채움률 < 0.93, 오목 깊이 > 0.06) 기준이다. 이 표는 원 채움률 0.94 시절에 쟀고, 0.93으로 바꾼 뒤에도 결손 25% 이상은 모두 같다.
오목 깊이를 넣기 전에는 결손 25% `bite`가 38%(지름 156px에서는 0%)만 잡혔다. 지금은 지름 156px에서도 100%다.

오목 깊이와 원 채움률을 결손 크기 10%(가장 작은 결손)와 여러 지름에서 따로 쟀다
(`python tools/simulator/shape_metrics.py --strength 0.10 0.25 --diameter 40 78 156`).

| 유형 (결손 10%) | 원 채움률 40 / 78 / 156px | 오목 깊이 40 / 78 / 156px |
|---|---|---|
| 정상 | 0.950 / 0.970 / 0.981 | 0.035 / 0.023 / 0.015 |
| `bite` | 0.93~0.94 / 0.95 / 0.96 | 0.08~0.15 / 0.10~0.14 / 0.11~0.13 |
| `cut` | 0.90~0.92 / 0.92~0.93 / 0.93~0.94 | 0.03~0.05 / 0.02~0.03 / 0.02 |
| `wedge` | 0.86~0.88 / 0.88~0.90 / 0.89~0.90 | 0.71~0.84 / 0.80~0.86 / 0.83~0.87 |
| `half` | 0.57~0.60 / 0.58~0.59 / 0.59~0.60 | 0.03~0.07 / 0.02~0.04 / 0.01~0.02 |

→ `CONCAVITY_THRESHOLD=0.06`(정상 최대 0.035 vs `bite` 최소 0.082).
`FILL_RATIO_THRESHOLD`는 처음에 0.94(이상적 마스크에서 정상 최소 0.950 vs `cut` 최대 0.942)로 정했다가 **0.93으로 내렸다** (2026-10-28, 박민성).
시연은 폰(640px)으로 모니터를 찍으므로 과자 지름이 약 40~50px로 작아지고, 그만큼 정상 과자의 원 채움률도 내려간다.
calib 프레임을 0.5배(지름 약 39px)로 줄여 SAM 2로 재면 정상 최소 0.934라 0.94에서는 오경보가 1건 나고, 0.93에서는 0건이다 (performance-plan 4장).
대가: 결손 10%처럼 아주 작은 `cut`을 지름 156px로 크게 찍으면 0.93~0.94라 놓칠 수 있다. 시연 설정(결손 55%)에서는 `cut`이 0.86~0.88이라 해당 없다.
`bite`(≤0.32)와 `wedge`(≥0.64)는 오목 깊이 0.45로, `cut`(≥0.72)과 `half`(≤0.60)는 원 채움률 0.70으로 나뉜다.

정리하면 다음과 같다.
- **정상 쿠키의 원형도는 1.0이 아니라 약 0.90이다.** 픽셀 외곽의 계단 때문에 둘레가 길게 계산되기 때문이다.
  해상도(`proc_size`)를 바꾸면 이 값도 바뀌므로 임계값을 다시 보정해야 한다.
- 원형도·solidity 두 지표만 쓰면 `bite`의 절반 이상과 `cut` 전부를 놓쳤다. 특히 `cut`은 두 지표 모두 정상과 겹친다.
  **원 채움률을 더해 `cut`과 결손 55% 이상의 `bite`를 모두 잡게 됐다.**
- 원형도·solidity·원 채움률만으로는 결손 25% 이하의 `bite`가 약점이었다 (세 지표 모두 정상과 0.01~0.02 차이).
  **오목 깊이를 더해 해결했다.** 결손 10% `bite`도 오목 깊이 0.08 이상으로, 정상(최대 0.035)과 겹치지 않는다 (위 표).
- 화면 가장자리에 걸친 과자는 잘린 모양으로 보여서 `half`나 `cut`처럼 측정된다. 판정에서 빼야 한다 (7.4).

#### 시뮬레이터 프레임 채점 결과 (2026-10-28)

`python tools/c3_eval.py <데이터 폴더> [--segmenter sam2 | --masks gt]`로 쟀다. 시뮬레이터 프레임 + 정답 라벨에서
화면 가장자리 과자는 빼고, 정답 bbox에 8% 패딩을 붙여 C2 박스 대신 넣었다 (C2 박스 검출과 무관하게 C3만 평가).
조건: 라인 3개·라인 간격 150px·속도 130px/s·지름 78px, 기본(결손 55%), 작은 결손(20%), 노이즈 50%, 조명 불균일 70%.

| 마스크 | 판정 규칙 | 불량 Recall (4조건) | 불량 Precision | 유형 정확도 | 마스크 IoU |
|---|---|---|---|---|---|
| 정답 폴리곤 | 원형도 + solidity | 0.29 ~ 0.72 (`cut` 전부, `bite` 다수 놓침) | 1.000 | — | — |
| 정답 폴리곤 | + 원 채움률 (3지표) | 1.000, 단 작은 결손 0.839 (`bite` 5개 전부 놓침) | 1.000 | — | — |
| 정답 폴리곤 | **4지표** | **1.000** | 1.000 | 1.000 | — |
| SAM 2 tiny | **4지표** | **1.000** | 1.000 | 1.000 | 0.972 ~ 0.974 |
| OpenCV 기준선 | **4지표** | 1.000 | 1.000 | 0.78 ~ 0.90 (`half`→`bite` 혼동) | 0.848 ~ 0.857 |

SAM 2 마스크가 OpenCV 윤곽선보다 외곽이 정확해(IoU 0.97 vs 0.85) 유형 구분까지 맞힌다 (7.2의 "SAM 2를 쓰는 근거").

### 7.4 화면 가장자리에 걸친 과자

시뮬레이터는 과자를 화면 오른쪽 밖에서 생성하고, 왼쪽 밖으로 완전히 나간 뒤에 지운다.
그래서 화면 양쪽 끝에는 항상 **일부만 보이는 과자**가 있다. 이 과자를 그대로 판정하면 정상 과자가 불량으로 나온다.
**처리 방법(확정)**: C4가 박스가 화면 끝(`EDGE_MARGIN`)에 닿은 과자를 `truncated`로 표시하고, 추적·집계·다수결에서 뺀다 (4.2).
과자 단위 판정은 온전히 보인 프레임들로만 정해진다. 프레임 단위 `summary`에는 여전히 들어가므로 프레임 단위 개수는 참고용이다.
C2에서 아예 빼지 않는 이유는, 들어오는 중인 과자도 화면에 표시해야 하고 평가 스크립트가 같은 기준으로 걸러낼 수 있어야 하기 때문이다.

### 7.5 CPU 환경에서의 Scale-out 한계
`docs/performance-plan.md` 참고. 요약하면, 복제본들이 같은 CPU 코어를 나눠 쓰므로
**C3 복제본 수 × `TORCH_NUM_THREADS`를 물리 코어 수(개발 PC 기준 8) 안으로 맞춰야** 하고,
처리량 증가는 선형이 아니라 금방 포화된다.
