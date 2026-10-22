#!/usr/bin/env bash
# ============================================================================
# 把 /ZZYProj/ 转发加入宿主机 nginx（需 sudo；先备份、先 nginx -t、再 reload）
# 用法：cd ZZYProj && ./deploy/nginx-install.sh
# 摘除：./deploy/nginx-uninstall.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

CONF="/etc/nginx/sites-available/learning-assistant"
SNIP="deploy/nginx-zzy.conf"
STAMP="$(date +%Y%m%d%H%M%S)"

if [ ! -f "$CONF" ]; then
  echo "❌ 找不到 $CONF"
  exit 1
fi
if [ ! -f "$SNIP" ]; then
  echo "❌ 找不到 $SNIP"
  exit 1
fi
if sudo grep -q "location /ZZYProj/" "$CONF"; then
  echo "ℹ️  /ZZYProj/ 已存在，未重复插入。"
  exit 0
fi

sudo cp "$CONF" "$CONF.bak-$STAMP"
echo "✅ 已备份：$CONF.bak-$STAMP"

sudo python3 - "$CONF" "$SNIP" <<'PY'
import sys
conf_path, snip_path = sys.argv[1], sys.argv[2]
conf = open(conf_path, encoding='utf-8').read()
snip = open(snip_path, encoding='utf-8').read()
marker = "    location / {"
idx = conf.find(marker)
if idx < 0:
    raise SystemExit("❌ 未找到 'location / {' 插入点")
# 插到 server_name kmv-learning-assistant.xyz 的那个 server 块里（第一个 location / 之前）
conf = conf[:idx] + snip.rstrip() + "\n\n" + conf[idx:]
open(conf_path, 'w', encoding='utf-8').write(conf)
print("✅ 已插入 /ZZYProj/ 转发")
PY

sudo nginx -t
sudo nginx -s reload
echo "✅ nginx 已 reload。回滚：sudo cp $CONF.bak-$STAMP $CONF && sudo nginx -t && sudo nginx -s reload"
