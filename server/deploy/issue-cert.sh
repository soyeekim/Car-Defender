#!/usr/bin/env bash
# Let's Encrypt 인증서를 처음 한 번 발급받는다. 갱신은 certbot 컨테이너가 알아서 한다.
#
# 사용법:  ./deploy/issue-cert.sh <이메일> [도메인]
# 예:      ./deploy/issue-cert.sh me@example.com api.fairway.click
set -euo pipefail

EMAIL="${1:?사용법: ./deploy/issue-cert.sh <이메일> [도메인]}"
DOMAIN="${2:-api.fairway.click}"
CD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
LIVE="/etc/letsencrypt/live/${DOMAIN}"

cd "$(dirname "$0")/.."

echo "==> 도메인이 이 서버를 가리키는지 확인"
MY_IP=$(curl -s --max-time 5 https://checkip.amazonaws.com || echo "")
DNS_IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || echo "")
echo "    서버 공인 IP : ${MY_IP:-확인 실패}"
echo "    $DOMAIN : ${DNS_IP:-조회 실패}"
if [ -n "$MY_IP" ] && [ -n "$DNS_IP" ] && [ "$MY_IP" != "$DNS_IP" ]; then
    echo "!!  A 레코드가 이 서버를 가리키지 않는다. 발급이 실패한다."
    echo "!!  Route 53에서 $DOMAIN 의 A 레코드를 $MY_IP 로 맞춘 뒤 다시 실행한다."
    exit 1
fi

if $CD run --rm --entrypoint sh certbot -c "[ -f ${LIVE}/fullchain.pem ]" 2>/dev/null; then
    echo "==> 이미 인증서가 있다. 다시 받으려면 아래를 먼저 실행한다."
    echo "    $CD run --rm --entrypoint sh certbot -c 'rm -rf ${LIVE} /etc/letsencrypt/archive/${DOMAIN} /etc/letsencrypt/renewal/${DOMAIN}.conf'"
    exit 0
fi

echo "==> 임시 자체 서명 인증서 생성 (nginx를 띄우기 위한 발판)"
# nginx는 인증서 파일이 없으면 시작하지 못하고, nginx가 없으면 인증 요청을 받을 수 없다.
# 그래서 가짜 인증서로 일단 띄운 뒤 진짜로 교체한다.
$CD run --rm --entrypoint sh certbot -c "
  mkdir -p ${LIVE} &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout ${LIVE}/privkey.pem -out ${LIVE}/fullchain.pem -subj '/CN=${DOMAIN}'
"

echo "==> nginx 기동"
$CD up -d nginx
sleep 3

echo "==> 임시 인증서 제거 후 실제 발급"
$CD run --rm --entrypoint sh certbot -c "rm -rf ${LIVE} /etc/letsencrypt/archive/${DOMAIN} /etc/letsencrypt/renewal/${DOMAIN}.conf"
$CD run --rm certbot certonly \
    --webroot -w /var/www/certbot \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos --no-eff-email --non-interactive

echo "==> nginx 재적용"
$CD up -d --force-recreate nginx
sleep 3

echo "==> 확인"
curl -sI "https://${DOMAIN}/api/v1/health" | head -1 || true
echo
echo "발급 완료. 갱신은 certbot 컨테이너가 12시간마다 자동으로 시도한다."
