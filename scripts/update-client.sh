#!/bin/sh

set -u

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
skill_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

if ! command -v git >/dev/null 2>&1; then
  echo "ai-design-update:skipped:git-missing"
  exit 10
fi

if [ ! -d "$skill_dir/.git" ]; then
  echo "ai-design-update:skipped:not-a-git-clone"
  exit 11
fi

origin_url=$(git -C "$skill_dir" remote get-url origin 2>/dev/null || true)
case "$origin_url" in
  https://github.com/chiko256/ai-design-skill|https://github.com/chiko256/ai-design-skill.git|git@github.com:chiko256/ai-design-skill.git)
    ;;
  *)
    echo "ai-design-update:skipped:unexpected-origin"
    exit 12
    ;;
esac

if [ -n "$(git -C "$skill_dir" status --porcelain --untracked-files=normal)" ]; then
  echo "ai-design-update:skipped:local-changes"
  exit 13
fi

if ! git -C "$skill_dir" fetch --quiet origin main; then
  echo "ai-design-update:failed:fetch"
  exit 14
fi

local_commit=$(git -C "$skill_dir" rev-parse HEAD)
remote_commit=$(git -C "$skill_dir" rev-parse origin/main)

if [ "$local_commit" = "$remote_commit" ]; then
  echo "ai-design-update:current"
  exit 0
fi

base_commit=$(git -C "$skill_dir" merge-base HEAD origin/main)
if [ "$local_commit" != "$base_commit" ]; then
  echo "ai-design-update:skipped:diverged"
  exit 15
fi

if ! git -C "$skill_dir" merge --ff-only --quiet origin/main; then
  echo "ai-design-update:failed:fast-forward"
  exit 16
fi

echo "ai-design-update:updated:$local_commit:$remote_commit"
