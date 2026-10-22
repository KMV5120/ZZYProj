#!/usr/bin/env bash
# ============================================================================
# G3 部署：备份 → 构建新镜像 → 保留上一版 pre-<ver> → 启动 → smoke
# 用法：./deploy/deploy.sh
# 回滚：./deploy/rollback.sh <上一版版本号>
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(grep -E '^ZZY_VERSION=' .env 2>/dev/null | cut -d= -f2- || echo ZZY_1.0.0)"
VERSION="${VERSION:-ZZY_1.0.0}"
COMPOSE="docker compose -f docker-compose.zzy.yml"

echo "[1/5] 备份数据库"
mkdir -p data/zzy
if [ -f data/zzy/zzy.db ]; then
  cp data/zzy/zzy.db "data/zzy/zzy.db.bak_$(date +%Y%m%d%H%M%S)"
  sha256sum data/zzy/zzy.db
fi

echo "[2/5] 保留上一版镜像（若存在）"
if docker image inspect zzy-proj:latest >/dev/null 2>&1; then
  PREV_ID="$(docker image inspect zzy-proj:latest --format '{{.Id}}')"
  docker tag "$PREV_ID" "zzy-proj:pre-$VERSION"
  echo "✅ 上一版已标记为 zzy-proj:pre-$VERSION"
fi

echo "[3/5] 构建并启动 $VERSION"
ZZY_IMAGE_TAG="$VERSION" $COMPOSE up -d --build

echo "[4/5] 等待健康检查"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8100/healthz >/dev/null 2>&1; then
    echo "✅ 容器健康"
    break
  fi
  sleep 1
  if [ "$i" = "30" ]; then
    echo "❌ 健康检查失败，请查看：$COMPOSE logs --tail=100"
    exit 1
  fi
done

echo "[5/5] 部署后 smoke"
curl -fsS http://127.0.0.1:8100/healthz
echo
curl -fsS -o /dev/null -w "页面 %{http_code}\n" http://127.0.0.1:8100/
curl -fsS -o /dev/null -w "JS   %{http_code}\n" http://127.0.0.1:8100/static/app.js
echo "✅ 部署完成。外部子路径 smoke 需在 nginx 安装后执行（A19）。"
