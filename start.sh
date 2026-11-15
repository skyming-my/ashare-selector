#!/usr/bin/env bash
# 智选A股 · Ubuntu / Debian 启动脚本
# 用法：chmod +x start.sh && ./start.sh
set -euo pipefail
cd "$(dirname "$0")"

# ---- 查找 Python 3.10+ ----
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$c"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "错误：未找到 Python 3.10 或更高版本。"
  echo "Ubuntu/Debian 安装：sudo apt update && sudo apt install -y python3"
  echo "当前 python3 版本：$(python3 --version 2>&1 || echo '未安装')"
  exit 1
fi

PORT="${ASHARE_PORT:-8765}"
HOST="${ASHARE_HOST:-127.0.0.1}"

echo "============================================"
echo "  智选A股  正在启动..."
echo "  Python : $("$PY" --version 2>&1)"
echo "  监听   : ${HOST}:${PORT}"
echo "  地址   : http://127.0.0.1:${PORT}"
echo "  停止   : Ctrl+C"
echo "============================================"

# 桌面环境自动打开浏览器；服务器/无桌面环境跳过
if [ -n "${DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then
  (
    sleep 2
    xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true
  ) &
fi

exec "$PY" app.py --no-browser "$@"
