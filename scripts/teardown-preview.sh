#!/bin/bash
# teardown-preview.sh — stop and clean up a preview slot.
#
# Invoked by server.py (teardown_preview tool) or by promote.sh after merge.
#     bash teardown-preview.sh <run_id>
#
# Stops the preview container, removes the built image, clears the slot lock.
set -uo pipefail

STATE_DIR="${ASHIGARU_STATE_DIR:-/home/devrunner/ashigaru}"
SLOTS_DIR="${ASHIGARU_SLOTS_DIR:-/srv/ashigaru/slots}"
HOST_PODMAN="${ASHIGARU_HOST_PODMAN:-unix:///run/host-podman/podman.sock}"

hpodman() { CONTAINER_HOST="$HOST_PODMAN" podman "$@"; }

meta_get() {
  python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "$1" "$2"
}

run_id="${1:?run_id required}"
rundir="${STATE_DIR}/runs/${run_id}"
meta="${rundir}/meta.json"

[ -f "$meta" ] || { echo "ERROR: no meta.json for run_id=${run_id}"; exit 1; }

slot_num="$(meta_get "$meta" preview_slot)"
[ -z "$slot_num" ] || [ "$slot_num" = "None" ] && { echo "No preview slot allocated for this run"; exit 0; }

slot="development${slot_num}"
slot_dir="${SLOTS_DIR}/${slot}"
image_tag="localhost/ashigaru-preview-${run_id}:latest"

echo "Tearing down ${slot} for run ${run_id} ..."

hpodman stop "$slot" 2>/dev/null || true
hpodman rm -f "$slot" 2>/dev/null || true

hpodman rmi "$image_tag" 2>/dev/null || true

# Clear lock and slot data
rm -f "${slot_dir}/lock.json"
rm -rf "${slot_dir}/data/pgdata" 2>/dev/null || true

echo "Slot ${slot} freed"
