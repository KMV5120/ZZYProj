#!/usr/bin/env bash
# ============================================================================
# 摘除 /ZZYProj/ 转发（下线用）；不会停止容器
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

CONF="/etc/nginx/sites-available/learning-assistant"
STAMP="$(date +%Y%m%d%H%M%S)"

if ! sudo grep -q "location /ZZYProj/" "$CONF"; then
  echo "ℹ️  未发现 /ZZYProj/ 配置，无需摘除。"
  exit 0
fi

sudo cp "$CONF" "$CONF.bak-$STAMP"
sudo python3 - "$CONF" <<'PY'
import sys
p = sys.argv[1]
lines = open(p, encoding='utf-8').read().splitlines(keepends=True)
out, skipping = [], False
for line in lines:
    if line.strip() == "location = /ZZYProj {":
        skipping = True
        continue
    if skipping:
        if line.strip() == "}":
            skipping = False
        continue
    if line.strip() == "location /ZZYProj/ {":
        skipping = True
        continue
    out.append(line)
open(p, 'w', encoding='utf-8').write(''.join(out))
print("✅ 已摘除 /ZZYProj/ 转发")
PY

sudo nginx -t
sudo nginx -s reload
echo "✅ nginx 已 reload。备份：$CONF.bak-$STAMP"
