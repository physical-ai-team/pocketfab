#!/bin/sh
# 자체 서명 인증서가 없거나 PUBLIC_HOST가 바뀌었으면 새로 만든다.
# nginx 공식 이미지가 시작할 때 /docker-entrypoint.d/*.sh 를 순서대로 실행한다.
set -e

CERT_DIR=/etc/nginx/certs
HOST="${PUBLIC_HOST:-localhost}"

if [ -f "$CERT_DIR/server.crt" ] && [ -f "$CERT_DIR/server.key" ] \
   && [ "$(cat "$CERT_DIR/host.txt" 2>/dev/null)" = "$HOST" ]; then
    echo "[cert] 기존 인증서 사용 ($HOST)"
    exit 0
fi

# IP 주소면 IP:, 아니면 DNS: 로 SAN을 넣는다 (브라우저는 CN이 아니라 SAN을 본다)
case "$HOST" in
    *[!0-9.]*) SAN="DNS:$HOST,DNS:localhost" ;;
    *)         SAN="IP:$HOST,DNS:localhost" ;;
esac

mkdir -p "$CERT_DIR"
openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
    -subj "/CN=$HOST" -addext "subjectAltName=$SAN" 2>/dev/null
echo "$HOST" > "$CERT_DIR/host.txt"
echo "[cert] 자체 서명 인증서 생성 ($SAN)"
