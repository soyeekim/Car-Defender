#!/usr/bin/env bash
# 새 이미지를 받아 배포한다. 헬스체크가 실패하면 이전 이미지로 되돌린다.
#
# 사용법:  ./deploy/deploy.sh            (.env의 APP_IMAGE를 그대로 씀)
#          ./deploy/deploy.sh myid/fairway-app:v3   (태그를 바꿔서 배포)
set -euo pipefail

cd "$(dirname "$0")/.."
CD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

[ -f .env ] || { echo "!! .env가 없다. cp .env.example .env 후 값을 채운다."; exit 1; }

if [ $# -ge 1 ]; then
    echo "==> APP_IMAGE를 $1 로 변경"
    if grep -q '^APP_IMAGE=' .env; then
        sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$1|" .env
    else
        printf '\nAPP_IMAGE=%s\n' "$1" >> .env
    fi
fi

NEW_IMAGE=$(grep '^APP_IMAGE=' .env | cut -d= -f2-)
[ -n "$NEW_IMAGE" ] || { echo "!! .env에 APP_IMAGE가 없다."; exit 1; }
echo "==> 배포 대상: $NEW_IMAGE"

# 롤백용으로 현재 돌고 있는 이미지를 기억해 둔다.
PREV_IMAGE=$($CD ps --format '{{.Image}}' app 2>/dev/null | head -1 || echo "")
echo "==> 현재 이미지: ${PREV_IMAGE:-없음(첫 배포)}"

echo "==> 이미지 받기"
$CD pull app

echo "==> 재시작"
$CD up -d --no-build

health() {
    $CD exec -T app python -c "
import sys, urllib.request
try:
    r = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)
    sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
" >/dev/null 2>&1
}

echo "==> 헬스체크 (최대 90초)"
for i in $(seq 1 30); do
    if health; then
        echo "    정상 (${i}회차)"
        echo
        echo "배포 완료: $NEW_IMAGE"
        $CD ps --format 'table {{.Service}}\t{{.Status}}'
        exit 0
    fi
    sleep 3
done

echo "!! 헬스체크 실패. 최근 로그:"
$CD logs --tail 40 app

if [ -n "$PREV_IMAGE" ] && [ "$PREV_IMAGE" != "$NEW_IMAGE" ]; then
    echo "!! $PREV_IMAGE 로 롤백한다."
    sed -i "s|^APP_IMAGE=.*|APP_IMAGE=$PREV_IMAGE|" .env
    $CD up -d --no-build
    echo "!! 롤백 완료. 문제를 고친 뒤 다시 배포한다."
else
    echo "!! 되돌릴 이전 이미지가 없다. 로그를 보고 직접 조치한다."
fi
exit 1
