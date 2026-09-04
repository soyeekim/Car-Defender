#!/usr/bin/env bash
# 새 EC2 인스턴스를 한 번만 준비한다. 사용자 데이터로 이미 도커를 깔았다면
# 이 스크립트는 빠진 것만 채우고 넘어간다.
#
# 사용법:  ./deploy/setup-ec2.sh
set -euo pipefail

echo "==> 스왑 확인"
if swapon --show 2>/dev/null | grep -q swapfile; then
    echo "    이미 있음"
else
    echo "    2GB 생성 (1GB 인스턴스에서 docker pull 중 메모리 부족을 막는다)"
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "==> 도커 확인"
if command -v docker >/dev/null; then
    echo "    이미 있음"
else
    sudo dnf install -y docker git
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    echo "!!  docker 그룹이 적용되려면 로그아웃 후 다시 접속해야 한다."
fi

echo "==> docker compose 플러그인 확인"
if docker compose version >/dev/null 2>&1; then
    echo "    이미 있음"
else
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    sudo curl -sSL \
        https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
fi

echo "==> 업로드 저장 디렉터리"
mkdir -p data

echo "==> .env 확인"
if [ -f .env ]; then
    echo "    이미 있음"
else
    cp .env.example .env
    echo "!!  .env를 만들었다. 아래 값을 채운 뒤 배포한다."
    echo "      APP_ENV=prod"
    echo "      JWT_SECRET      32자 이상 랜덤 (openssl rand -hex 32)"
    echo "      APP_IMAGE       <도커허브계정>/fairway-app:v1"
    echo "      SMTP_*          SES SMTP 자격 증명"
    echo "      MAIL_BACKEND=smtp"
fi

echo
echo "준비 완료. 다음 순서로 진행한다."
echo "  1. .env 값 채우기"
echo "  2. docker login                     (비공개 저장소일 때)"
echo "  3. ./deploy/issue-cert.sh <이메일>   (인증서 발급)"
echo "  4. ./deploy/deploy.sh               (배포)"
