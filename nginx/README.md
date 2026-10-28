# Nginx — 담당: 김세광

| 포트 | 역할 |
|---|---|
| 80 | 443으로 리다이렉트 |
| 443 | 외부 진입점. HTTPS·WebSocket을 C1으로 프록시 |
| 8080 | 내부 전용(호스트에 공개 안 함). C3 복제본 로드밸런싱 (`least_conn`) |

- **인증서**: 시작할 때 `generate-cert.sh`가 `PUBLIC_HOST`용 자체 서명 인증서를 `nginx/certs/`에 만든다.
  `PUBLIC_HOST`가 바뀌면 다시 만든다. git에는 올라가지 않는다.
- **Scale-out**: `upstream`의 `resolve` 옵션으로 Docker DNS를 10초마다 다시 조회하므로,
  `docker compose up -d --scale c3-inference=N`으로 복제본 수를 바꾸면 Nginx 재시작 없이 반영된다.
- **바쁜 복제본 처리**: C3가 추론 중이면 503을 돌려주고, Nginx가 다음 복제본으로 넘긴다(`proxy_next_upstream http_503`).
  모든 복제본이 바쁘면 503이 C2로 가고, C2는 그 프레임을 버린다.

## 남은 일
- 성능 측정 시 `proxy_next_upstream_tries`, `keepalive` 값 튜닝
- 시연 환경에서 인증서 경고를 없애려면 mkcert 같은 로컬 CA 사용 검토
