# upstream의 'resolve' 옵션(C3 scale 자동 반영)은 nginx 1.27.3 이상 오픈소스판에서 지원된다
FROM nginx:1.28-alpine

RUN apk add --no-cache openssl
COPY default.conf /etc/nginx/conf.d/default.conf
COPY generate-cert.sh /docker-entrypoint.d/15-generate-cert.sh
# Windows에서 체크아웃하면 실행 권한이 없으므로 여기서 준다 (없으면 nginx 엔트리포인트가 건너뜀)
RUN chmod +x /docker-entrypoint.d/15-generate-cert.sh
