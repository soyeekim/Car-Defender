#!/usr/bin/env bash
# 메일 발송 공급자를 SES <-> Gmail 로 갈아끼운다. 코드 변경도 재빌드도 없다.
#
# 사용법:  ./deploy/switch-mail.sh gmail <지메일주소> <앱비밀번호16자>
#          ./deploy/switch-mail.sh ses
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:?사용법: ./deploy/switch-mail.sh gmail <주소> <앱비밀번호> | ses}"
CD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

set_env() {  # set_env KEY VALUE — 있으면 치환, 없으면 추가
    if grep -q "^$1=" .env; then
        sed -i "s|^$1=.*|$1=$2|" .env
    else
        echo "$1=$2" >> .env
    fi
}

case "$MODE" in
gmail)
    ADDR="${2:?지메일 주소를 넣어라}"
    PASS="${3:?앱 비밀번호 16자를 넣어라}"
    # SES 설정을 되돌릴 수 있게 한 번만 백업한다.
    [ -f .env.ses.bak ] || cp .env .env.ses.bak
    set_env SMTP_HOST smtp.gmail.com
    set_env SMTP_PORT 587
    set_env SMTP_STARTTLS true
    set_env SMTP_USER "$ADDR"
    set_env SMTP_PASSWORD "$PASS"
    # Gmail은 인증한 계정 주소로 From을 덮어쓴다. 맞춰두지 않으면 표시가 어긋난다.
    set_env MAIL_FROM "$ADDR"
    echo "==> Gmail SMTP로 전환. 아무 주소로나 발송된다 (하루 500통)."
    ;;
ses)
    [ -f .env.ses.bak ] || { echo "!! .env.ses.bak이 없다. 되돌릴 SES 설정이 없다."; exit 1; }
    cp .env.ses.bak .env
    echo "==> SES로 되돌렸다."
    ;;
*)
    echo "!! 첫 인자는 gmail 또는 ses 여야 한다."; exit 1 ;;
esac

$CD up -d app
echo "==> 앱 재시작 완료. 현재 설정:"
grep -E "^(SMTP_HOST|SMTP_USER|MAIL_FROM)=" .env
