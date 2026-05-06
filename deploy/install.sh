#!/usr/bin/env bash
# deploy/install.sh — systemd 서비스 등록 (WSL2 + Ubuntu)
# 사용법:
#   sudo bash deploy/install.sh <리눅스_유저명>
# 예시:
#   sudo bash deploy/install.sh zeta
set -euo pipefail

USER_NAME="${1:?사용법: $0 <유저명>}"
SERVICE_NAME="jongbae"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}@.service"
REPO_DIR="/home/${USER_NAME}/Legendary_method"

echo "[1/4] 서비스 파일 복사 → ${SERVICE_FILE}"
sed "s|%i|${USER_NAME}|g" "${REPO_DIR}/deploy/jongbae.service" \
    | sudo tee "${SERVICE_FILE}" > /dev/null

echo "[2/4] logrotate 설정 복사"
sudo cp "${REPO_DIR}/deploy/logrotate.conf" /etc/logrotate.d/jongbae

echo "[3/4] systemd reload + 서비스 활성화"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}@${USER_NAME}"

echo "[4/4] 서비스 시작"
sudo systemctl start "${SERVICE_NAME}@${USER_NAME}"

echo ""
echo "완료. 상태 확인:"
echo "  sudo systemctl status ${SERVICE_NAME}@${USER_NAME}"
echo "  journalctl -u ${SERVICE_NAME}@${USER_NAME} -f"
