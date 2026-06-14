#!/bin/bash
# promote.sh — ship a reviewed PR to production by merging it (squash).
#
# Baked into the mcp-ashigaru image at /app/scripts/. Invoked by server.py as:
#     bash promote.sh <repo> <pr>
#
# Trust-based promotion, designed for phone-driven ops: there is NO approval
# token to type. Authorization is the maintainer's Signal instruction to
# Kagetora, and everything Kagetora reasons over is airlock-filtered. The merge
# is the promotion — the repo's GHA pipeline builds/ships from the default
# branch. (The old human-approval-marker gate was removed: nothing ever enrolled
# the marker, and a hex token is unusable from a phone.)
set -uo pipefail

ORG="${ASHIGARU_ORG:-crunchtools}"
repo="${1:?repo required}"
pr="${2:?pr number required}"
repo="${repo##*/}"   # accept "org/repo" — strip the org

if [ -f "${HOME}/.config/dev-runner/claude.env" ]; then
  # shellcheck source=/dev/null
  set -a; . "${HOME}/.config/dev-runner/claude.env"; set +a
fi
: "${GH_TOKEN:?missing GH_TOKEN}"

# Un-draft if needed (ignore failure when already ready), then squash-merge.
# gh needs an explicit -R: this runs with no repo checkout as the cwd.
GH_TOKEN="$GH_TOKEN" gh pr ready "$pr" -R "${ORG}/${repo}" 2>&1 | tail -1
GH_TOKEN="$GH_TOKEN" gh pr merge "$pr" -R "${ORG}/${repo}" --squash --delete-branch 2>&1 | tail -3
exit "${PIPESTATUS[0]}"
