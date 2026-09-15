#!/usr/bin/env bash
# 一键把「每天早上 9:00 检查一次」写进当前用户的 crontab。
# 重复执行不会写重复的条目。
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$PROJECT_DIR/scripts/run_daily.sh"
HOUR="${1:-9}"
MINUTE="${2:-0}"
ENTRY="$MINUTE $HOUR * * * $RUNNER"

chmod +x "$RUNNER"

existing="$(crontab -l 2>/dev/null || true)"
if grep -Fq "$RUNNER" <<<"$existing"; then
  echo "crontab 里已有该任务，先移除旧条目再写入新的。"
  existing="$(grep -Fv "$RUNNER" <<<"$existing")"
fi

printf '%s\n%s\n' "$existing" "$ENTRY" | sed '/^$/d' | crontab -

echo "已添加定时任务：每天 $(printf '%02d:%02d' "$HOUR" "$MINUTE") 检查一次"
echo "查看：crontab -l"
echo "日志：$PROJECT_DIR/logs/"
