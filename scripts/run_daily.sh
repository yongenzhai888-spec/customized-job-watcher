#!/usr/bin/env bash
# 供 cron 调用的包装脚本：切到项目目录、用项目自带的 Python 执行一次监测，并留下日志。
#
#   crontab -e
#   0 9 * * * /绝对路径/job-watcher/scripts/run_daily.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi

mkdir -p logs
LOG_FILE="logs/watch-$(date +%Y-%m).log"

{
  echo "──────── $(date '+%Y-%m-%d %H:%M:%S') ────────"
  PYTHONPATH="$PROJECT_DIR/src" "$PYTHON" -m jobwatch run "$@"
} >>"$LOG_FILE" 2>&1
