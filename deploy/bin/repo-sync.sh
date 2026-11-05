#!/usr/bin/env bash
# 同步 REPOS_ROOT 底下所有乾淨 clone 到 target branch 最新，並增量重建
# code graph（裝了 code-review-graph 才會跑）。由 systemd timer 定期執行。
set -u
REPOS_ROOT="${REPOS_ROOT:-/opt/repos}"

for d in "$REPOS_ROOT"/*/; do
  [ -d "$d/.git" ] || continue
  name="$(basename "$d")"
  if git -C "$d" pull --ff-only --quiet; then
    sha="$(git -C "$d" rev-parse --short HEAD)"
    echo "[repo-sync] $name -> $sha"
  else
    echo "[repo-sync] $name pull 失敗（分支發散或網路），維持原狀" >&2
    continue
  fi
  if command -v code-review-graph >/dev/null 2>&1; then
    if (cd "$d" && code-review-graph build >/dev/null 2>&1); then
      echo "[repo-sync] $name graph 增量重建完成"
    else
      echo "[repo-sync] $name graph build 失敗（不影響 review，僅少 graph 上下文）" >&2
    fi
  fi
done
