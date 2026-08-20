#!/usr/bin/env bash
# Boot-smoke a bundled datasource plugin image before rollout: assert signed,
# signature covers the backend binary, exec bit set, and the binary actually execs.
# Usage: smoke-test-bundled-plugin.sh <image-ref> <plugin-id>
set -euo pipefail

IMAGE="${1:?usage: smoke-test-bundled-plugin.sh <image-ref> <plugin-id>}"
PLUGIN_ID="${2:?usage: smoke-test-bundled-plugin.sh <image-ref> <plugin-id>}"

PLUGIN_DIR="/usr/share/grafana/data/datasource-plugins/${PLUGIN_ID}"
PLATFORM="linux/amd64"

workdir="$(mktemp -d)"
create_cid=""
run_cid=""
cleanup() {
  if [ -n "${run_cid}" ]; then docker rm -f "${run_cid}" >/dev/null 2>&1 || true; fi
  if [ -n "${create_cid}" ]; then docker rm -f "${create_cid}" >/dev/null 2>&1 || true; fi
  rm -rf "${workdir}"
}
trap cleanup EXIT

fail() { echo "::error::$*"; exit 1; }

if docker image inspect "${IMAGE}" >/dev/null 2>&1; then
  echo "== Using locally built ${IMAGE} (skipping pull) =="
else
  echo "== Pulling ${IMAGE} =="
  docker pull --platform "${PLATFORM}" "${IMAGE}" >/dev/null
fi

echo "== Extracting plugin dist from the image =="
# Distroless image (no shell): copy files out of a non-running container.
create_cid="$(docker create --platform "${PLATFORM}" "${IMAGE}")"
docker cp "${create_cid}:${PLUGIN_DIR}" "${workdir}/dist" \
  || fail "plugin dist not found at ${PLUGIN_DIR} in the image"
dist="${workdir}/dist"

echo "== Check 1: plugin is signed (MANIFEST.txt) =="
manifest="${dist}/MANIFEST.txt"
[ -f "${manifest}" ] \
  || fail "MANIFEST.txt missing: plugin is unsigned and the apiserver will refuse to load it"
grep -q '"signatureType": *"grafana"' "${manifest}" \
  || fail "MANIFEST.txt is not a grafana-type signature"

echo "== Locate the linux/amd64 backend binary =="
binary=""
for f in "${dist}"/gpx_*_linux_amd64; do
  if [ -e "${f}" ]; then binary="$(basename "${f}")"; break; fi
done
[ -n "${binary}" ] || fail "no gpx_*_linux_amd64 backend binary under ${PLUGIN_DIR}"
echo "backend binary: ${binary}"

echo "== Check 2: backend binary is covered by the signature =="
grep -q "\"${binary}\"" "${manifest}" \
  || fail "${binary} is not listed in MANIFEST.txt (signature does not cover the running binary)"

echo "== Check 3: backend binary has the executable bit =="
[ -x "${dist}/${binary}" ] \
  || fail "${binary} is not executable (would crashloop with 'permission denied')"

echo "== Check 4: backend binary actually executes =="
# Exec-proof only, not a full apiserver boot. Run standalone (no go-plugin
# magic-cookie env): a healthy backend logs "Serving plugin" then exits non-zero,
# so assert on output, not exit code. Re-check markers when the SDK is bumped.
run_cid="$(docker run -d --platform "${PLATFORM}" \
  --entrypoint "${PLUGIN_DIR}/${binary}" "${IMAGE}")"
sleep 8
out="$(docker logs "${run_cid}" 2>&1 || true)"
echo "---- backend output ----"
echo "${out}"
echo "------------------------"

if echo "${out}" | grep -Eqi 'permission denied|exec format error|cannot execute'; then
  fail "backend binary failed to execute"
fi
if ! echo "${out}" | grep -Eqi 'Serving plugin|this binary is a plugin'; then
  fail "backend binary did not reach plugin startup (see output above)"
fi

echo "Smoke test passed: ${IMAGE}"
