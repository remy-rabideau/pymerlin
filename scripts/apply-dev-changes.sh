#!/usr/bin/env bash
#
# DEV ONLY. Apply local pymerlin changes to a running plandev stack:
#
#   1. rebuild the shim JAR into pymerlin/_internal/jars/   (Java changes)
#   2. repackage the model JAR around that shim             (Java + model changes)
#   3. restart the three containers that run Python         (library Python changes)
#
# Assumes plandev is running under docker-compose.dev.yml, which bind-mounts this checkout
# over the venv's pymerlin. That mount is why nothing here builds an image: Python edits
# are already inside the containers the moment you save them, and only need the restart in
# step 3 because a running JVM holds the modules it has already imported.
#
# Steps 1-2 exist because the shim is the one piece a mount cannot deliver. The classes
# that execute during a simulation come from the UPLOADED model JAR -- MissionModelLoader
# opens a URLClassLoader over the JAR in merlin's file store -- not from any path inside
# the container. So a Java change has to travel: source -> shim JAR -> model JAR -> upload.
#
# That last hop is the one thing this script cannot do for you; it prints the reminder.
#
# Usage:
#   scripts/apply-dev-changes.sh
#
# Env:
#   PLANDEV_PATH  plandev checkout (default: sibling of this repo).
#                 Same variable java/pymerlin-shim/build.gradle reads.
#   MODEL_REF     model to package, path:Class (default: demo/model.py:Mission)
#   MODEL_JAR     output JAR path (default: <repo parent>/model.jar)
#   SKIP_PACKAGE  set to 1 for a Python-only change -- skips steps 1-2
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLANDEV_ROOT="${PLANDEV_PATH:-$(cd "${REPO_ROOT}/.." && pwd)/plandev}"
MODEL_REF="${MODEL_REF:-demo/model.py:Mission}"
MODEL_JAR="${MODEL_JAR:-$(cd "${REPO_ROOT}/.." && pwd)/model.jar}"
SKIP_PACKAGE="${SKIP_PACKAGE:-0}"

SERVICES=(aerie_merlin aerie_merlin_worker_1 aerie_merlin_worker_2)
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

# The venv copy first: it is the one installed from this checkout, so it packages with the
# JAR just built rather than whatever pymerlin happens to be on PATH.
PYMERLIN_BIN="${REPO_ROOT}/venv/bin/pymerlin"
[ -x "${PYMERLIN_BIN}" ] || PYMERLIN_BIN="$(command -v pymerlin || true)"

log() { echo "[apply-dev-changes] $*"; }

if [ ! -f "${PLANDEV_ROOT}/docker-compose.dev.yml" ]; then
  log "ERROR: no plandev checkout with a dev overlay at ${PLANDEV_ROOT}"
  log "       Set PLANDEV_PATH=/path/to/plandev and re-run."
  exit 1
fi

if [ "${SKIP_PACKAGE}" != "1" ]; then
  log "1/3 rebuilding shim JAR"
  "${REPO_ROOT}/scripts/build-shim.sh"

  if [ -z "${PYMERLIN_BIN}" ]; then
    log "ERROR: no pymerlin CLI found (looked for ${REPO_ROOT}/venv/bin/pymerlin, then PATH)"
    exit 1
  fi
  log "2/3 repackaging ${MODEL_REF} -> ${MODEL_JAR}"
  # MODEL_REF is relative to the repo root, so package from there regardless of caller cwd.
  (cd "${REPO_ROOT}" && "${PYMERLIN_BIN}" package --model "${MODEL_REF}" --out "${MODEL_JAR}")
else
  log "1-2/3 skipped (SKIP_PACKAGE=1)"
fi

cd "${PLANDEV_ROOT}"

# A container created before the bind mounts existed keeps running the pymerlin baked into
# its image, and `restart` will not attach a mount to it -- it silently keeps serving the
# stale copy, which surfaces much later as an unexplained TypeError at model upload rather
# than as an error here. Detect that case and recreate instead. No image build either way.
MOUNT_TARGET="/opt/pymerlin/python-resources/venv/lib/python3.12/site-packages/pymerlin"
NEEDS_RECREATE=0
for svc in "${SERVICES[@]}"; do
  if ! docker inspect "${svc}" \
       --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' 2>/dev/null \
       | grep -qx "${MOUNT_TARGET}"; then
    NEEDS_RECREATE=1
    log "${svc}: dev mount absent"
  fi
done

if [ "${NEEDS_RECREATE}" = "1" ]; then
  log "3/3 recreating containers to attach the dev mounts"
  "${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"
else
  log "3/3 restarting containers"
  "${COMPOSE[@]}" restart "${SERVICES[@]}"
fi

log "done"
if [ "${SKIP_PACKAGE}" != "1" ]; then
  log "the shim only reaches a simulation through the model JAR -- re-upload ${MODEL_JAR}"
fi
