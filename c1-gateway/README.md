# C1 Gateway — 담당: 이기태

스마트폰 카메라 페이지, QR 접속, 결과 대시보드, 프레임 유입 제어(백프레셔)를 맡는다.
스펙: [docs/api-spec.md](../docs/api-spec.md) 3장, 4장 ①④, 5장

## 파일
| 파일 | 내용 |
|---|---|
| `app/main.py` | 서버. 카메라 WebSocket 수신 → C2 전송, C4 결과 수신 → 대시보드 중계 |
| `app/static/camera.html` | 스마트폰 카메라 페이지 (`/camera`) |
| `app/static/dashboard.html` | 대시보드 (`/`) |

## 어디부터 채우면 되나
뼈대는 동작한다. 아래 순서로 다듬으면 된다.
1. **`camera.html`** — 실제 폰(안드로이드/아이폰)에서 카메라가 켜지는지, 세로 화면·후면 카메라 선택·해상도를 확인하고 다듬는다.
2. **`dashboard.html`** — 불량 개수 누적, 최근 불량 프레임 목록, 처리 fps 그래프 등 시연용 화면을 꾸민다.
3. **`main.py`** — 지금은 잘 동작하므로 크게 건드릴 필요 없다. `MAX_IN_FLIGHT`는 C3 복제본 수에 맞춰 `.env`에서 조정한다.

## 혼자 확인하기
```powershell
docker compose up --build
python tools/fake_camera.py      # 폰 없이 합성 영상 전송
```
`https://localhost/` 대시보드에 `fake N` 프레임이 보이면 정상이다.
