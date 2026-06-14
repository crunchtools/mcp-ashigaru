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
# branch. After merging, tears down any associated preview slot.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
ORG="${ASHIGARU_ORG:-crunchtools}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
repo="${1:?repo required}"
pr="${2:?pr number required}"
repo="${repo##*/}"

if [ -f "${HOME}/.config/dev-runner/claude.env" ]; then
  # shellcheck source=/dev/null
  set -a; . "${HOME}/.config/dev-runner/claude.env"; set +a
fi
: "${GH_TOKEN:?missing GH_TOKEN}"

GH_TOKEN="$GH_TOKEN" gh pr ready "$pr" -R "${ORG}/${repo}" 2>&1 | tail -1
GH_TOKEN="$GH_TOKEN" gh pr merge "$pr" -R "${ORG}/${repo}" --squash --delete-branch 2>&1 | tail -3
merge_rc="${PIPESTATUS[0]}"

# Teardown any preview slot associated with this PR's run.
if [ "$merge_rc" -eq 0 ]; then
  for rundir in "${STATE_DIR}/runs/"*/; do
    [ -f "${rundir}meta.json" ] || continue
    run_repo="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('repo',''))" "${rundir}meta.json" 2>/dev/null)"
    run_pr="$(python3 -c "import json,sys; u=json.load(open(sys.argv[1])).get('pr_url',''); print(u.rstrip('/').rsplit('/',1)[-1] if u else '')" "${rundir}meta.json" 2>/dev/null)"
    if [ "$run_repo" = "$repo" ] && [ "$run_pr" = "$pr" ]; then
      run_id="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('run_id',''))" "${rundir}meta.json" 2>/dev/null)"
      if [ -n "$run_id" ]; then
        bash "${SCRIPT_DIR}/teardown-preview.sh" "$run_id" 2>/dev/null || true
        python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m["phase"]="shipped"; json.dump(m,open(sys.argv[1],"w"))' "${rundir}meta.json" 2>/dev/null || true
      fi
      break
    fi
  done
fi

exit "$merge_rc"
