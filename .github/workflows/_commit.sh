#!/usr/bin/env bash
# data/ の変更をリポジトリに保存する
set -euo pipefail
git config user.name "threads-affiliate-bot"
git config user.email "threads-affiliate-bot@users.noreply.github.com"
git add data/
if git diff --cached --quiet; then exit 0; fi
git commit -m "$1"
for i in 1 2 3; do
  git pull --rebase && git push && exit 0
  sleep $((i * 5))
done
exit 1
