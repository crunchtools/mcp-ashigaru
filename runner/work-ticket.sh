#!/bin/bash
# work-ticket.sh — the deterministic dispatcher for one Ashigaru dev run.
#
# Baked into the mcp-ashigaru image at /app/runner/. Invoked by server.py as:
#     bash work-ticket.sh <repo> <issue>
#
# It mints a run_id, clones the repo onto the shared STATE volume, then DETACHES
# the long-running work (agent + gates + PR) and returns the run_id immediately
# on stdout (last line) so the MCP call doesn't block. Poll status(run_id) after.
#
# Topology note: the worker container is launched via podman talking to the
# bind-mounted *rootless devrunner* socket (CONTAINER_HOST). Its `-v` mounts are
# resolved host-side, so the clone MUST live under STATE_DIR, which is mounted at
# the same path here and visible to devrunner's podman.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
ORG="${ASHIGARU_ORG:-crunchtools}"
AGENT_IMAGE="${ASHIGARU_AGENT_IMAGE:-localhost/rotv-dev-runner:latest}"
MODEL="${ANTHROPIC_MODEL:-sonnet}"

# Tokens normally arrive via the container env-file (systemd). Fall back to the
# devrunner env file if present (host/native runs).
if [ -f "${HOME}/.config/dev-runner/claude.env" ]; then
  set -a; . "${HOME}/.config/dev-runner/claude.env"; set +a
fi

# ---- helpers -------------------------------------------------------------
set_phase() {  # set_phase <rundir> <phase>
  local rundir="$1" phase="$2" tmp
  tmp="$(mktemp)"
  if command -v jq >/dev/null 2>&1; then
    jq --arg p "$phase" '.phase=$p' "${rundir}/meta.json" >"$tmp" && mv "$tmp" "${rundir}/meta.json"
  else
    python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m["phase"]=sys.argv[2]; json.dump(m,open(sys.argv[1],"w"))' \
      "${rundir}/meta.json" "$phase"
  fi
}
meta_set() {  # meta_set <rundir> <key> <json-value>
  local rundir="$1" key="$2" val="$3"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m[sys.argv[2]]=json.loads(sys.argv[3]); json.dump(m,open(sys.argv[1],"w"))' \
    "${rundir}/meta.json" "$key" "$val"
}

# ---- background run (re-exec of this script) -----------------------------
if [ "${1:-}" = "--run" ]; then
  shift
  run_id="$1" repo="$2" issue="$3" branch="$4"
  rundir="${STATE_DIR}/runs/${run_id}"
  repodir="${STATE_DIR}/work/${run_id}/${repo}"

  : "${CLAUDE_CODE_OAUTH_TOKEN:?missing CLAUDE_CODE_OAUTH_TOKEN}"
  : "${GH_TOKEN:?missing GH_TOKEN}"

  cd "$repodir" || { set_phase "$rundir" failed; exit 1; }
  git checkout -B "$branch" >/dev/null 2>&1

  # Pull the issue text to brief the agent.
  set_phase "$rundir" diagnosing
  issue_text="$(GH_TOKEN="$GH_TOKEN" gh issue view "$issue" -R "${ORG}/${repo}" \
                  --json title,body -q '"#\(.number? // "'"$issue"'") \(.title)\n\n\(.body)"' 2>>"${rundir}/setup.log")"
  [ -z "$issue_text" ] && issue_text="GitHub issue #${issue} in ${ORG}/${repo} (issue body unavailable; read the repo and infer)."

  prompt="You are fixing GitHub issue #${issue} in the ${repo} repository (crunchtools fleet).

ISSUE:
${issue_text}

TASK:
1. Read the relevant code to understand the bug.
2. Diagnose the root cause and fix it with a MINIMAL, focused change. Do not refactor unrelated code.
3. You CANNOT run the test suite or any commands in this container (Read/Edit/Write/Glob/Grep only). Reason by reading; the PR's CI runs the gates after you finish.
End with a short summary: root cause + exactly what you changed."

  # Launch the sealed worker (rootless, as devrunner via the mounted socket).
  set_phase "$rundir" editing
  podman run --rm \
    -v "${STATE_DIR}/work/${run_id}/${repo}:/work:z" -w /work \
    -e CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
    "$AGENT_IMAGE" \
    claude -p "$prompt" --model "$MODEL" --permission-mode acceptEdits \
      --allowedTools "Read,Edit,Write,Glob,Grep" --max-turns 40 \
      --output-format stream-json --verbose \
      >>"${rundir}/events.jsonl" 2>>"${rundir}/agent.err"

  if git diff --quiet && git diff --cached --quiet; then
    meta_set "$rundir" gate '"no-change"'
    set_phase "$rundir" failed
    exit 0
  fi

  # Commit, push, open a DRAFT PR. The PR's CI is the real gate; a human reviews.
  set_phase "$rundir" gating
  base="$(GH_TOKEN="$GH_TOKEN" gh repo view "${ORG}/${repo}" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null)"
  base="${base:-main}"
  git add -A
  git -c user.email="devrunner@crunchtools.com" -c user.name="Kagetora Dev Runner" \
    commit -q -m "fix: address issue #${issue} (Ashigaru autonomous run)"
  git push -u "https://x-access-token:${GH_TOKEN}@github.com/${ORG}/${repo}.git" "$branch" \
    >>"${rundir}/setup.log" 2>&1

  body="Fixes #${issue}.

🤖 Autonomous **Ashigaru** run — Kagetora dispatched Claude Code (\`${MODEL}\`) in a sealed, unprivileged sandbox. Diagnosed by code-reading only (no test execution in-sandbox). **Draft, pending CI + human review.** run_id: \`${run_id}\`"
  pr_url="$(GH_TOKEN="$GH_TOKEN" gh pr create --draft -R "${ORG}/${repo}" \
              --base "$base" --head "$branch" \
              --title "fix: issue #${issue} (Ashigaru)" \
              --body "$body" 2>>"${rundir}/setup.log")"

  [ -n "$pr_url" ] && meta_set "$rundir" pr_url "\"${pr_url}\""
  set_phase "$rundir" awaiting-approval
  exit 0
fi

# ---- foreground setup ----------------------------------------------------
repo="${1:?repo required}"
issue="${2:?issue required}"
: "${GH_TOKEN:?missing GH_TOKEN}"

run_id="${repo//[^a-z0-9-]/-}-${issue}-$(date +%s)"
rundir="${STATE_DIR}/runs/${run_id}"
repodir="${STATE_DIR}/work/${run_id}/${repo}"
branch="fix/issue-${issue}"
mkdir -p "$rundir" "$(dirname "$repodir")"

cat >"${rundir}/meta.json" <<EOF
{"run_id":"${run_id}","repo":"${repo}","issue":${issue},"branch":"${branch}","phase":"cloning","pr_url":null,"gate":null,"created":"$(date -Is)"}
EOF

if ! git clone --depth 50 \
      "https://x-access-token:${GH_TOKEN}@github.com/${ORG}/${repo}.git" "$repodir" \
      >>"${rundir}/setup.log" 2>&1; then
  set_phase "$rundir" failed
  echo "$run_id"   # still return the id so status() can show the failure
  exit 0
fi

# Detach the heavy work; redirect everything so the MCP call's pipe closes.
setsid bash "$0" --run "$run_id" "$repo" "$issue" "$branch" \
  >>"${rundir}/runner.log" 2>&1 </dev/null &
disown

echo "$run_id"
