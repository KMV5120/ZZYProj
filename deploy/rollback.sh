#!/usr/bin/env bash
# ============================================================================
# G3 回滚：把上一版镜像重新指为 latest 并重启（数据卷不动）
# 用法：./deploy/rollback.sh ZZY_1.0.0
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${1:-}"
if [ -z "$TAG" ]; then
  echo "用法：./deploy/rollback.sh pre-<版本号>"
  docker image ls 'zzy-proj' --format '  {{.Repository}}:{{.Tag}}  {{.ID}}  {{.CreatedAt}}'
  exit 2
fi

if ! docker image inspect "zzy-proj:$TAG" >/dev/null 2>&1; then
  echo "❌ 镜像 zzy-proj:$TAG 不存在"
  exit 1
fi

echo "[1/3] 回滚到 zzy-proj:$TAG"
docker tag "zzy-proj:$TAG" zzy-proj:latest

echo "[2/3] 重启容器（不重建、不动数据卷）"
ZZY_IMAGE_TAG=latest docker compose -f docker-compose.zzy.yml up -d --no-build

echo "[3/3] 健康检查"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8100/healthz >/dev/null 2>&1; then
    echo "✅ 已回滚并恢复"
    exit 0
  fi
  sleep 1
done
echo "❌ 回滚后健康检查失败"
exit 1
