## 변경 내용
<!-- 무엇을 왜 바꿨는지 2~5줄 -->

## 확인 방법
<!-- 직접 확인한 것에 체크하고, 해당 없는 항목은 지운다 -->
- [ ] `docker compose up -d --build --scale c3-inference=2` 후 `python tools/fake_camera.py --seconds 10`에서 처리 fps > 0
- [ ] `c3-inference`에서 `pytest` 통과
- [ ] 폰으로 접속해서 확인

## 스펙 변경
- [ ] 없음
- [ ] `docs/api-spec.md` 변경 (영향 받는 서비스: )
- [ ] 환경변수 추가·변경 (`.env.example`, api-spec 6장 반영)
