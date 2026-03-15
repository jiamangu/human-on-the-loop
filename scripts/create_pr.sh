#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/create_pr.sh --title "PR title" [--body-file path] [--base main] [--draft]

Description:
  Push the current branch to origin and create a GitHub pull request with gh.

Notes:
  - This script expects your changes to already be committed.
  - If you run it on main, it will stop to avoid opening a PR from the default branch.
  - Required tools: git, gh
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

TITLE=""
BODY_FILE=""
BASE_BRANCH="main"
DRAFT_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --title)
      TITLE="${2:-}"
      shift 2
      ;;
    --body-file)
      BODY_FILE="${2:-}"
      shift 2
      ;;
    --base)
      BASE_BRANCH="${2:-}"
      shift 2
      ;;
    --draft)
      DRAFT_FLAG="--draft"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$TITLE" ]]; then
  echo "--title is required." >&2
  usage >&2
  exit 1
fi

if [[ -n "$BODY_FILE" && ! -f "$BODY_FILE" ]]; then
  echo "Body file not found: $BODY_FILE" >&2
  exit 1
fi

require_cmd git
require_cmd gh

CURRENT_BRANCH="$(git branch --show-current)"

if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "Could not determine current branch." >&2
  exit 1
fi

if [[ "$CURRENT_BRANCH" == "$BASE_BRANCH" ]]; then
  echo "Current branch is $BASE_BRANCH. Please create a feature branch before opening a PR." >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Please commit or stash changes before creating a PR." >&2
  exit 1
fi

echo "Pushing branch $CURRENT_BRANCH to origin..."
git push -u origin "$CURRENT_BRANCH"

PR_ARGS=(
  pr create
  --base "$BASE_BRANCH"
  --head "$CURRENT_BRANCH"
  --title "$TITLE"
)

if [[ -n "$BODY_FILE" ]]; then
  PR_ARGS+=(--body-file "$BODY_FILE")
else
  PR_ARGS+=(--fill)
fi

if [[ -n "$DRAFT_FLAG" ]]; then
  PR_ARGS+=("$DRAFT_FLAG")
fi

echo "Creating pull request..."
gh "${PR_ARGS[@]}"
