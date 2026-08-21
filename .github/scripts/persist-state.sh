#!/usr/bin/env bash
# Commit SQLite + jobs.json and push. Long scrapes often lose a race to other
# merges on main; rebase onto origin and retry instead of failing the job
# (which also drops the un-pushed checkpoint).
set -euo pipefail

msg="${1:?commit message required}"
branch="${GITHUB_REF_NAME:-main}"
remote="${GIT_PERSIST_REMOTE:-origin}"
max="${PERSIST_PUSH_ATTEMPTS:-5}"

git config user.name "hzz-digest-bot"
git config user.email "actions@users.noreply.github.com"

added=0
if [ -f data/hzz_jobs.sqlite3 ]; then
  git add -- data/hzz_jobs.sqlite3
  added=1
fi
if [ -f docs/jobs.json ]; then
  git add -- docs/jobs.json
  added=1
fi
if [ "$added" -eq 0 ]; then
  echo "persist: no state files present"
  exit 0
fi
if git diff --cached --quiet; then
  echo "persist: no staged changes"
  exit 0
fi
git commit -m "$msg"

keep_this_run_state() {
  # During rebase, --theirs is the commit being replayed (this run).
  git checkout --theirs -- data/hzz_jobs.sqlite3 2>/dev/null || true
  git checkout --theirs -- docs/jobs.json 2>/dev/null || true
  git add -- data/hzz_jobs.sqlite3 docs/jobs.json 2>/dev/null || true
}

rebase_onto_remote() {
  git fetch -- "$remote" "$branch"
  if git rebase "${remote}/${branch}"; then
    return 0
  fi
  echo "persist: rebase conflict; keeping this run's SQLite and jobs.json"
  keep_this_run_state
  if ! GIT_EDITOR=true git rebase --continue; then
    echo "persist: could not finish rebase" >&2
    git rebase --abort || true
    return 1
  fi
}

attempt=1
while true; do
  if ! rebase_onto_remote; then
    exit 1
  fi
  if git push -- "$remote" "HEAD:${branch}"; then
    echo "persist: pushed"
    exit 0
  fi
  echo "persist: push rejected (attempt ${attempt}/${max})"
  if [ "$attempt" -ge "$max" ]; then
    echo "persist: giving up after ${max} push attempts" >&2
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep "${PERSIST_RETRY_SLEEP:-$((attempt * 2))}"
done
