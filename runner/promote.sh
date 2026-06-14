#!/bin/bash
# promote.sh — the GATED, irreversible action: ship a reviewed PR to production.
#
# Baked into the mcp-ashigaru image at /app/runner/. Invoked by server.py as:
#     bash promote.sh <pr> <approval_token>
#
# Hard gate: refuses unless <approval_token> matches the human-approval marker
# recorded for this PR at ${ASHIGARU_STATE_DIR}/approvals/<pr>. That marker is
# written ONLY by a confirmed human approval (Signal -> Kagetora), never by the
# coding agent and never by the LLM-facing tool surface. A confused or injected
# caller therefore cannot ship.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
ORG="${ASHIGARU_ORG:-crunchtools}"
REPO="${ASHIGARU_PROMOTE_REPO:-}"   # optional default repo for merge

pr="${1:?pr number required}"
approval_token="${2:?approval_token required}"

if [ -f "${HOME}/.config/dev-runner/claude.env" ]; then
  set -a; . "${HOME}/.config/dev-runner/claude.env"; set +a
fi
: "${GH_TOKEN:?missing GH_TOKEN}"

marker="${STATE_DIR}/approvals/${pr}"
if [ ! -f "$marker" ]; then
  echo "REFUSED: no human-approval marker for PR #${pr}"
  exit 3
fi
expected="$(cat "$marker")"
if [ "$expected" != "$approval_token" ]; then
  echo "REFUSED: approval_token does not match the recorded marker for PR #${pr}"
  exit 3
fi

# Approved. Un-draft and merge; the repo's merge-to-default GHA performs the
# actual rollout. (Repo-specific post-merge rollout, if any, is a later step.)
repo_flag=()
[ -n "$REPO" ] && repo_flag=(-R "${ORG}/${REPO}")

GH_TOKEN="$GH_TOKEN" gh pr ready "$pr" "${repo_flag[@]}" 2>&1 | tail -1
GH_TOKEN="$GH_TOKEN" gh pr merge "$pr" "${repo_flag[@]}" --squash --delete-branch 2>&1 | tail -3
rc=${PIPESTATUS[0]}

# Consume the marker so an approval can't be replayed.
rm -f "$marker"
exit "$rc"
