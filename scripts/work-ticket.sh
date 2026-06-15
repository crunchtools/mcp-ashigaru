#!/bin/bash
# work-ticket.sh — the deterministic dispatcher for one Ashigaru dev run.
#
# Baked into the mcp-ashigaru image at /app/scripts/. Invoked by server.py as:
#     bash work-ticket.sh <repo> <issue> <brief> [model]     # new run
#     bash work-ticket.sh --iterate <run_id>                  # iterate on existing
#
# New runs: mints a run_id, clones the repo, DETACHES the background work, and
# returns the run_id immediately. The background phase runs the escalation ladder
# (Sonnet → Opus → Opus-max → escalate to human) automatically.
#
# Iterations: re-invokes the sealed worker on the existing branch with feedback
# (foreman notes + prior gate failure), escalating to the next model tier.
#
# SECURITY MODEL (issue #4):
#   - The GitHub issue is NEVER read here. The caller passes the already-airlock-
#     filtered issue text as <brief>; the sealed sub-agent only sees <brief>.
#   - The sub-agent is sealed: ONLY its Claude token (no GH token, no podman
#     socket). This script holds the GH token and performs all git/gh work.
#   - Network stays open for the Anthropic API.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
ORG="${ASHIGARU_ORG:-crunchtools}"
AGENT_IMAGE="${ASHIGARU_AGENT_IMAGE:-localhost/rotv-dev-runner:latest}"
# shellcheck disable=SC2034
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -f "${HOME}/.config/dev-runner/claude.env" ]; then
  # shellcheck source=/dev/null
  set -a; . "${HOME}/.config/dev-runner/claude.env"; set +a
fi

# The clone is chowned to UID 1000 (for the worker) but git operations in
# --run/--iterate run as root. Suppress the dubious-ownership check.
git config --global --add safe.directory '*' 2>/dev/null || true

# ---- model tier table -------------------------------------------------------
# Tier 1: Sonnet (cheap first pass)
# Tier 2: Opus (fed prior diff + gate failure)
# Tier 3: Opus with extended turns (maximum automated effort)
tier_model() {
  case "${1:-1}" in
    1) echo "${ANTHROPIC_MODEL:-claude-sonnet-4-6}" ;;
    2) echo "claude-opus-4-6" ;;
    3) echo "claude-opus-4-6" ;;
    *) echo "claude-opus-4-6" ;;
  esac
}
tier_max_turns() {
  case "${1:-1}" in
    1) echo 40 ;;
    2) echo 60 ;;
    3) echo 80 ;;
    *) echo 80 ;;
  esac
}
MAX_TIER=3

# ---- helpers ----------------------------------------------------------------
set_phase() {
  local rundir="$1" phase="$2"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m["phase"]=sys.argv[2]; json.dump(m,open(sys.argv[1],"w"))' \
    "${rundir}/meta.json" "$phase"
}
meta_set() {
  local rundir="$1" key="$2" val="$3"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m[sys.argv[2]]=json.loads(sys.argv[3]); json.dump(m,open(sys.argv[1],"w"))' \
    "${rundir}/meta.json" "$key" "$val"
}
meta_get() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$1" "$2"
}
append_attempt() {
  local rundir="$1" tier="$2" model="$3" gate="$4" gate_output="$5"
  python3 -c '
import json,sys
m = json.load(open(sys.argv[1]))
a = m.get("attempts", [])
a.append({"tier": int(sys.argv[2]), "model": sys.argv[3], "gate": sys.argv[4], "gate_output": sys.argv[5][:500]})
m["attempts"] = a
m["current_tier"] = int(sys.argv[2])
json.dump(m, open(sys.argv[1], "w"))
' "${rundir}/meta.json" "$tier" "$model" "$gate" "$gate_output"
}

# Run the sealed Claude Code worker. Outputs to events.jsonl.
run_agent() {
  local repodir="$1" rundir="$2" prompt="$3" model="$4" max_turns="$5"
  set_phase "$rundir" editing
  podman run --rm \
    -v "${repodir}:/work:z" -w /work \
    -e CLAUDE_CODE_OAUTH_TOKEN="$CLAUDE_CODE_OAUTH_TOKEN" \
    -e HOME=/home/user \
    "$AGENT_IMAGE" \
    claude -p "$prompt" --model "$model" --permission-mode dontAsk \
      --allowedTools "Read,Edit,Write,Bash,Glob,Grep" --max-turns "$max_turns" \
      --output-format stream-json --verbose \
      >>"${rundir}/events.jsonl" 2>>"${rundir}/agent.err"
}

# Check if the agent produced any changes (basic gate).
check_gate() {
  local repodir="$1"
  cd "$repodir" || return 1
  if git diff --quiet && git diff --cached --quiet; then
    echo "no-change"
    return 1
  fi
  echo "passed"
  return 0
}

# Commit and push changes to the PR branch.
commit_and_push() {
  local repodir="$1" rundir="$2" issue="$3" branch="$4" force="${5:-}"
  cd "$repodir" || return 1
  git add -A
  git -c user.email="devrunner@crunchtools.com" -c user.name="Kagetora Dev Runner" \
    commit -q -m "fix: address issue #${issue} (Ashigaru autonomous run)" --allow-empty-message
  local push_flags="-u"
  [ "$force" = "force" ] && push_flags="-u --force-with-lease"
  git push $push_flags "https://x-access-token:${GH_TOKEN}@github.com/${ORG}/$(basename "$repodir").git" "$branch" \
    >>"${rundir}/setup.log" 2>&1
}

# ---- --iterate mode (re-invoke on existing branch) --------------------------
if [ "${1:-}" = "--iterate" ]; then
  shift
  run_id="${1:?run_id required}"
  rundir="${STATE_DIR}/runs/${run_id}"
  meta="${rundir}/meta.json"

  [ -f "$meta" ] || { echo "ERROR: no meta.json"; exit 1; }

  : "${CLAUDE_CODE_OAUTH_TOKEN:?missing CLAUDE_CODE_OAUTH_TOKEN}"
  : "${GH_TOKEN:?missing GH_TOKEN}"

  repo="$(meta_get "$meta" repo)"
  issue="$(meta_get "$meta" issue)"
  branch="$(meta_get "$meta" branch)"
  current_tier="$(meta_get "$meta" current_tier)"
  current_tier="${current_tier:-1}"
  repodir="${STATE_DIR}/work/${run_id}/${repo}"

  # Human feedback (notes provided) = new direction, not a failure. Stay at the
  # current tier. Only escalate when auto-retrying from a gate failure (no notes).
  has_feedback=false
  [ -s "${rundir}/feedback.txt" ] && has_feedback=true

  if $has_feedback; then
    next_tier="$current_tier"
  else
    next_tier=$((current_tier + 1))
    if [ "$next_tier" -gt "$MAX_TIER" ]; then
      set_phase "$rundir" escalated
      echo "All ${MAX_TIER} tiers exhausted — escalating to human"
      exit 0
    fi
  fi

  model="$(tier_model "$next_tier")"
  max_turns="$(tier_max_turns "$next_tier")"

  cd "$repodir" || { set_phase "$rundir" failed; exit 1; }

  brief="$(cat "${rundir}/brief.txt" 2>/dev/null)"
  feedback="$(cat "${rundir}/feedback.txt" 2>/dev/null)"
  prior_diff="$(git diff HEAD~1..HEAD 2>/dev/null | head -200)"

  # Build the last attempt's gate output
  last_gate_output="$(python3 -c '
import json,sys
m = json.load(open(sys.argv[1]))
attempts = m.get("attempts", [])
print(attempts[-1].get("gate_output","") if attempts else "")
' "$meta" 2>/dev/null)"

  prompt="You are iterating on GitHub issue #${issue} in the ${repo} repository (crunchtools fleet).

ORIGINAL TASK:
${brief}

YOUR PRIOR ATTEMPT (diff of what was changed):
${prior_diff}

FEEDBACK FROM REVIEWER:
${feedback:-No specific feedback — the prior attempt did not pass the quality gate.}

GATE FAILURE REASON:
${last_gate_output:-Unknown}

INSTRUCTIONS:
1. Read the relevant code to understand what was already changed and what went wrong.
2. Fix the issues described in the feedback. Keep changes minimal and focused.
3. You can Read/Edit/Write/Bash/Glob/Grep. Use Bash to verify your changes. No network or git access.
End with a short summary: what you fixed this iteration."

  run_agent "$repodir" "$rundir" "$prompt" "$model" "$max_turns"

  gate_result="$(check_gate "$repodir")"
  if [ "$gate_result" = "passed" ]; then
    append_attempt "$rundir" "$next_tier" "$model" "passed" ""
    commit_and_push "$repodir" "$rundir" "$issue" "$branch" force
    set_phase "$rundir" awaiting-approval
    rm -f "${rundir}/feedback.txt"
    # Rebuild preview if a slot is allocated
    preview_slot="$(meta_get "$meta" preview_slot)"
    if [ -n "$preview_slot" ] && [ "$preview_slot" != "None" ]; then
      SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
      bash "${SCRIPT_DIR}/deploy-preview.sh" "$run_id" >>"${rundir}/preview.log" 2>&1 || true
    fi
  else
    append_attempt "$rundir" "$next_tier" "$model" "failed" "$gate_result"
    set_phase "$rundir" failed
  fi
  exit 0
fi

# ---- --run mode (background, first attempt + auto-escalation) ----------------
if [ "${1:-}" = "--run" ]; then
  shift
  run_id="$1" repo="$2" issue="$3" branch="$4" model="${5:-sonnet}"
  rundir="${STATE_DIR}/runs/${run_id}"
  repodir="${STATE_DIR}/work/${run_id}/${repo}"

  : "${CLAUDE_CODE_OAUTH_TOKEN:?missing CLAUDE_CODE_OAUTH_TOKEN}"
  : "${GH_TOKEN:?missing GH_TOKEN}"

  cd "$repodir" || { set_phase "$rundir" failed; exit 1; }
  git checkout -B "$branch" >/dev/null 2>&1

  brief="$(cat "${rundir}/brief.txt" 2>/dev/null)"
  if [ -z "$brief" ]; then
    meta_set "$rundir" gate '"no-brief"'
    set_phase "$rundir" failed
    exit 1
  fi

  # Resolve the starting model to a tier
  case "$model" in
    *sonnet*) start_tier=1 ;;
    *opus*)   start_tier=2 ;;
    *)               start_tier=1 ;;
  esac
  actual_model="$(tier_model "$start_tier")"
  max_turns="$(tier_max_turns "$start_tier")"

  prompt="You are fixing GitHub issue #${issue} in the ${repo} repository (crunchtools fleet).

TASK (the filtered issue brief provided by the maintainer):
${brief}

INSTRUCTIONS:
1. Read the relevant code to understand the problem.
2. Diagnose the root cause and fix it with a MINIMAL, focused change. Do not refactor unrelated code.
3. You can Read/Edit/Write/Bash/Glob/Grep. Use Bash to check syntax, run linters, or verify your changes. You have no network access beyond the Anthropic API, and no git/gh access — the dispatcher handles git operations after you finish.
End with a short summary: root cause + exactly what you changed."

  # ---- Tier 1 attempt -------------------------------------------------------
  run_agent "$repodir" "$rundir" "$prompt" "$actual_model" "$max_turns"

  gate_result="$(check_gate "$repodir")"
  if [ "$gate_result" != "passed" ]; then
    append_attempt "$rundir" "$start_tier" "$actual_model" "failed" "$gate_result"
    # No auto-escalation — the foreman decides whether to retry, escalate, or
    # give feedback. Use request_changes(run_id, notes) to iterate, or pass
    # model="opus" in a new work_ticket to start at a higher tier.
    set_phase "$rundir" failed
    exit 0
  else
    append_attempt "$rundir" "$start_tier" "$actual_model" "passed" ""
  fi

  set_phase "$rundir" gating
  base="$(GH_TOKEN="$GH_TOKEN" gh repo view "${ORG}/${repo}" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null)"
  base="${base:-main}"
  commit_and_push "$repodir" "$rundir" "$issue" "$branch"

  tier_reached="$(meta_get "${rundir}/meta.json" current_tier)"
  tier_reached="${tier_reached:-$start_tier}"
  used_model="$(tier_model "$tier_reached")"

  body="Fixes #${issue}.

🤖 Autonomous **Ashigaru** run — Kagetora dispatched Claude Code (\`${used_model}\`, tier ${tier_reached}) in a sealed, unprivileged sandbox. Worked from an airlock-filtered brief; diagnosed by code-reading only (no test execution in-sandbox). **Draft, pending CI + human review.** run_id: \`${run_id}\`"
  pr_url="$(GH_TOKEN="$GH_TOKEN" gh pr create --draft -R "${ORG}/${repo}" \
              --base "$base" --head "$branch" \
              --title "fix: issue #${issue} (Ashigaru)" \
              --body "$body" 2>>"${rundir}/setup.log")"

  [ -n "$pr_url" ] && meta_set "$rundir" pr_url "\"${pr_url}\""
  set_phase "$rundir" awaiting-approval
  exit 0
fi

# ---- foreground setup (new run) ---------------------------------------------
repo="${1:?repo required}"
issue="${2:?issue required}"
brief="${3:?brief required}"
model="${4:-${ANTHROPIC_MODEL:-sonnet}}"
repo="${repo##*/}"   # accept "org/repo" — strip the org
: "${GH_TOKEN:?missing GH_TOKEN}"

run_id="${repo//[^a-z0-9-]/-}-${issue}-$(date +%s)"
rundir="${STATE_DIR}/runs/${run_id}"
repodir="${STATE_DIR}/work/${run_id}/${repo}"
branch="fix/issue-${issue}"
mkdir -p "$rundir" "$(dirname "$repodir")"

printf '%s' "$brief" > "${rundir}/brief.txt"

cat >"${rundir}/meta.json" <<EOF
{"run_id":"${run_id}","repo":"${repo}","issue":${issue},"branch":"${branch}","model":"${model}","phase":"cloning","pr_url":null,"gate":null,"preview_url":null,"preview_slot":null,"current_tier":1,"attempts":[],"created":"$(date -Is)"}
EOF

if ! git clone --depth 50 \
      "https://x-access-token:${GH_TOKEN}@github.com/${ORG}/${repo}.git" "$repodir" \
      >>"${rundir}/setup.log" 2>&1; then
  set_phase "$rundir" failed
  echo "$run_id"
  exit 0
fi

# The worker runs as UID 1000 (non-root for bypassPermissions). The clone is
# root-owned; chown so the agent can actually write to the files.
chown -R 1000:1000 "$repodir" "${rundir}"

setsid bash "$0" --run "$run_id" "$repo" "$issue" "$branch" "$model" \
  >>"${rundir}/runner.log" 2>&1 </dev/null &
disown

echo "$run_id"
