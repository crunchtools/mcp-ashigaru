#!/bin/bash
# deploy-preview.sh — launch an ephemeral webapp preview for an Ashigaru run.
#
# Baked into the mcp-ashigaru image at /app/scripts/. Invoked by server.py as:
#     bash deploy-preview.sh <run_id>
#
# Allocates a free slot (development1-5), builds the image from the PR branch,
# seeds the DB from production, and launches the container. The preview is
# reachable at https://development{N}.crunchtools.com via the reverse proxy.
#
# TOPOLOGY: This script runs inside the mcp-ashigaru container (root-managed,
# crunchtools network). Two podman sockets are available:
#   CONTAINER_HOST  — devrunner's rootless socket (for sealed worker containers)
#   HOST_PODMAN     — the host root socket (for preview containers and prod access)
#
# Preview containers run on HOST podman because they need the `rotv` network and
# ports bound to the host loopback — same as production and test ROTV containers.
# Image builds also use HOST podman so the preview image is visible to the host.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
SLOTS_DIR="${ASHIGARU_SLOTS_DIR:-/srv/ashigaru/slots}"
CONFIG_DIR="${ASHIGARU_CONFIG_DIR:-/srv/ashigaru/config}"
TEMPLATE="${CONFIG_DIR}/preview-rotv-template.env"
HOST_PODMAN="${ASHIGARU_HOST_PODMAN:-unix:///run/host-podman/podman.sock}"
MAX_SLOTS=5
PROD_CONTAINER="rootsofthevalley.org"
ROTV_BASE_IMAGE="${ASHIGARU_ROTV_BASE_IMAGE:-quay.io/crunchtools/rotv-base:latest}"

# All podman commands in this script use the host socket.
hpodman() { CONTAINER_HOST="$HOST_PODMAN" podman "$@"; }

# ---- helpers ----------------------------------------------------------------
meta_get() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$1" "$2"
}
meta_set() {
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m[sys.argv[2]]=json.loads(sys.argv[3]); json.dump(m,open(sys.argv[1],"w"))' \
    "$1" "$2" "$3"
}
set_phase() {
  meta_set "$1/meta.json" phase "\"$2\""
}

# ---- main -------------------------------------------------------------------
run_id="${1:?run_id required}"
rundir="${STATE_DIR}/runs/${run_id}"
meta="${rundir}/meta.json"

[ -f "$meta" ] || { echo "ERROR: no meta.json for run_id=${run_id}"; exit 1; }

repo="$(meta_get "$meta" repo)"
branch="$(meta_get "$meta" branch)"
repodir="${STATE_DIR}/work/${run_id}/${repo}"

[ -d "$repodir" ] || { echo "ERROR: work dir missing: ${repodir}"; exit 1; }

# ---- allocate a free slot ---------------------------------------------------
slot=""
slot_num=""
for n in $(seq 1 $MAX_SLOTS); do
  lockfile="${SLOTS_DIR}/development${n}/lock.json"
  if [ ! -f "$lockfile" ]; then
    slot="development${n}"
    slot_num="$n"
    break
  fi
  # Check if the lock is stale (container not running)
  locked_container="$(python3 -c "import json; print(json.load(open('${lockfile}')).get('container',''))" 2>/dev/null)"
  if [ -n "$locked_container" ] && ! hpodman inspect "$locked_container" >/dev/null 2>&1; then
    rm -f "$lockfile"
    slot="development${n}"
    slot_num="$n"
    break
  fi
done

if [ -z "$slot" ]; then
  echo "ERROR: all ${MAX_SLOTS} preview slots are busy"
  exit 1
fi

slot_dir="${SLOTS_DIR}/${slot}"
port=$((8099 + slot_num))  # slot 1 → 8100, slot 2 → 8101, ...
preview_url="https://${slot}.crunchtools.com"

echo "Allocated ${slot} (port ${port}) for run ${run_id}"

# Write lock
cat > "${slot_dir}/lock.json" << EOF
{"run_id":"${run_id}","repo":"${repo}","branch":"${branch}","container":"${slot}","slot":${slot_num},"port":${port},"allocated_at":"$(date -Is)"}
EOF

set_phase "$rundir" deploying-preview
meta_set "$meta" preview_slot "$slot_num"
meta_set "$meta" preview_url "\"${preview_url}\""

# ---- build image from PR branch --------------------------------------------
echo "Building image from ${repodir} ..."
image_tag="localhost/ashigaru-preview-${run_id}:latest"

if ! hpodman build \
    --build-arg "BASE_IMAGE=${ROTV_BASE_IMAGE}" \
    -t "$image_tag" "$repodir" \
    >> "${rundir}/preview.log" 2>&1; then
  echo "ERROR: image build failed (see ${rundir}/preview.log)"
  rm -f "${slot_dir}/lock.json"
  set_phase "$rundir" failed
  exit 1
fi

echo "Image built: ${image_tag}"

# ---- stop any existing container on this slot --------------------------------
hpodman stop "$slot" 2>/dev/null
hpodman rm -f "$slot" 2>/dev/null

# ---- seed DB from production ------------------------------------------------
echo "Seeding DB from ${PROD_CONTAINER} ..."
if ! hpodman exec "$PROD_CONTAINER" pg_dump -U rotv rotv > "${slot_dir}/data/seed.sql" 2>>"${rundir}/preview.log"; then
  echo "WARNING: prod DB dump failed; preview will start with empty DB"
fi

# ---- generate slot env file -------------------------------------------------
sed "s|SLOT_NUM|${slot_num}|g" "$TEMPLATE" > "${slot_dir}/rotv.env"

# ---- launch preview container -----------------------------------------------
echo "Launching ${slot} on port ${port} ..."
if ! hpodman run -d \
    --systemd=always \
    --memory=2g --memory-swap=2g \
    --name "$slot" \
    --network rotv \
    -p "127.0.0.1:${port}:8080" \
    --env-file "${slot_dir}/rotv.env" \
    -v "${slot_dir}/data:/data:Z" \
    -v "${slot_dir}/rotv.env:/etc/rotv/environment:Z,ro" \
    "$image_tag" \
    >> "${rundir}/preview.log" 2>&1; then
  echo "ERROR: container launch failed"
  rm -f "${slot_dir}/lock.json"
  set_phase "$rundir" failed
  exit 1
fi

# ---- restore DB seed --------------------------------------------------------
echo "Waiting for PostgreSQL to initialize ..."
# Wait for PostgreSQL to be ready (not just the container — PG itself)
for _wait in $(seq 1 30); do
  if hpodman exec "$slot" su - postgres -c "pg_isready" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# Patch initDatabase to skip on seeded DBs (SKIP_INIT_DB=true in env).
# This works around the ROTV FK constraint ordering bug on restored dumps.
hpodman exec "$slot" sed -i 's/await initDatabase();/if (!process.env.SKIP_INIT_DB) await initDatabase();/' /app/server.js 2>>"${rundir}/preview.log" || true
hpodman exec "$slot" systemctl stop rotv-backend 2>>"${rundir}/preview.log" || true

if [ -s "${slot_dir}/data/seed.sql" ]; then
  echo "Restoring production DB seed ..."
  # Create the rotv role if it doesn't exist (seed.sql may or may not include it)
  hpodman exec "$slot" su - postgres -c "createuser rotv 2>/dev/null; createdb -O rotv rotv 2>/dev/null" 2>>"${rundir}/preview.log" || true
  hpodman exec -i "$slot" psql -U rotv rotv < "${slot_dir}/data/seed.sql" >>"${rundir}/preview.log" 2>&1 || true
  echo "DB seed restored"
fi

hpodman exec "$slot" systemctl start rotv-backend 2>>"${rundir}/preview.log" || true
sleep 5

# ---- healthcheck ------------------------------------------------------------
echo "Healthchecking http://127.0.0.1:${port}/ ..."
healthy=false
for _try in $(seq 1 12); do
  if curl -sf "http://127.0.0.1:${port}/" >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 5
done

if $healthy; then
  echo "Preview healthy at ${preview_url}"
  set_phase "$rundir" on-dev
else
  echo "WARNING: healthcheck failed after 60s; preview may still be starting"
  set_phase "$rundir" on-dev
fi

echo "${preview_url}"
