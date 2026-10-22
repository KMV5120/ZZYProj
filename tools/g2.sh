#!/usr/bin/env bash
# ============================================================================
# G2 合并/发版闸：一条命令跑完自动化部分（真实浏览器点击是人工步骤）
# 用法：PYTHON=/path/to/python ./tools/g2.sh
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c "import fastapi, httpx, jwt, bcrypt, pytest" >/dev/null 2>&1; then
  echo "❌ 当前 Python 缺少依赖。请设置 PYTHON 指向已安装 server/requirements-dev.txt 的解释器。"
  exit 2
fi

echo "========================================"
echo "  G2 · ZZyProj 平台化"
echo "========================================"

echo ""
echo "[1/3] 单元 + 契约测试（pytest）"
PYTHONPATH="$PWD/server" "$PYTHON" -m pytest -q server/tests

echo ""
echo "[2/3] 真实 HTTP 契约 smoke（假上游 + 真服务进程）"
"$PYTHON" tools/contract_smoke.py

echo ""
echo "[3/3] 前端 jsdom smoke（需要 NODE_PATH 指向含 jsdom 的 node_modules）"
if [ -n "${NODE_PATH:-}" ] && command -v node >/dev/null 2>&1; then
  node tools/frontend_smoke.cjs
else
  echo "  SKIP 未设置 NODE_PATH；跳过 jsdom（真实浏览器项仍需人工）"
fi

echo ""
echo "✅ G2 自动化部分通过。"
echo "   仍需人工完成：真实浏览器逐控件点击（A19/控件清单）+ 截图入 evidence/。"
