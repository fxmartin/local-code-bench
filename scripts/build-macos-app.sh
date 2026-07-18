#!/usr/bin/env bash
# Reproducible signed build pipeline for dist/LocalCodeBench.app
# (Stories 18.1-002 + 18.3-001): builds the SwiftUI shell in release mode,
# fetches a checksum-verified relocatable CPython (python-build-standalone),
# installs the freshly built harness wheel into Contents/Resources/python, and
# codesigns every bundled Mach-O inside-out with the hardened runtime and the
# app's entitlements. The emitted bundle passes `codesign --verify --deep`.
#
# Usage:
#   scripts/build-macos-app.sh                 build + sign dist/<app>.app
#   scripts/build-macos-app.sh --print-config  print the resolved config, exit
#
# Every version/path/identity pin lives in configs/build.yaml (Epic-15
# principle); environment variables of the same upper-case name (PBS_TAG,
# PBS_PYTHON, PBS_ARCH, PBS_SHA256, CODESIGN_IDENTITY) override it for
# one-off experiments. Signing uses the configured identity, else the first
# "Developer ID Application" certificate in the keychain, else falls back to
# an ad-hoc signature — clearly labeled UNSIGNED FOR DISTRIBUTION (local use
# only). Requires: swift (Command Line Tools), uv, curl, shasum, codesign.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_CONFIG="${REPO_ROOT}/configs/build.yaml"

# Read a flat scalar key from configs/build.yaml: strip the "key: " prefix,
# a trailing comment, and surrounding quotes. Nested keys are unsupported.
config_value() {
    sed -n -E "s/^$1:[[:space:]]*//p" "${BUILD_CONFIG}" \
        | head -1 \
        | sed -E 's/[[:space:]]+#.*$//; s/^"(.*)"$/\1/'
}

require_config() {
    local key="$1" value
    value="$(config_value "${key}")"
    if [[ -z "${value}" && "${key}" != "codesign_identity" ]]; then
        echo "error: ${key} missing from ${BUILD_CONFIG}" >&2
        exit 1
    fi
    printf '%s' "${value}"
}

APP_NAME="$(require_config app_name)"
BUNDLE_ID="$(require_config bundle_id)"
MIN_MACOS="$(require_config min_macos)"
PBS_TAG="${PBS_TAG:-$(require_config pbs_tag)}"
PBS_PYTHON="${PBS_PYTHON:-$(require_config pbs_python)}"
PBS_ARCH="${PBS_ARCH:-$(require_config pbs_arch)}"
PBS_SHA256="${PBS_SHA256:-$(require_config pbs_sha256)}"
ENTITLEMENTS="${REPO_ROOT}/$(require_config entitlements)"
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-$(config_value codesign_identity)}"

PBS_ARCHIVE="cpython-${PBS_PYTHON}+${PBS_TAG}-${PBS_ARCH}-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_ARCHIVE}"
APP_DIR="${REPO_ROOT}/dist/${APP_NAME}.app"
CACHE_DIR="${REPO_ROOT}/.runtime/pbs-cache"
APP_VERSION="$(cd "${REPO_ROOT}" && uv version --short)"

# Resolve the signing identity: config/env pin, else the first Developer ID
# Application certificate, else ad-hoc ("-").
SIGNING_MODE="developer-id"
if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    CODESIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
        | grep "Developer ID Application" | head -1 | awk '{print $2}' || true)"
fi
if [[ -z "${CODESIGN_IDENTITY}" ]]; then
    CODESIGN_IDENTITY="-"
fi
if [[ "${CODESIGN_IDENTITY}" == "-" ]]; then
    SIGNING_MODE="ad-hoc"
fi

if [[ "${1:-}" == "--print-config" ]]; then
    cat <<CONFIG
APP_NAME=${APP_NAME}
BUNDLE_ID=${BUNDLE_ID}
MIN_MACOS=${MIN_MACOS}
APP_VERSION=${APP_VERSION}
PBS_TAG=${PBS_TAG}
PBS_PYTHON=${PBS_PYTHON}
PBS_ARCH=${PBS_ARCH}
PBS_SHA256=${PBS_SHA256}
ENTITLEMENTS=${ENTITLEMENTS}
CODESIGN_IDENTITY=${CODESIGN_IDENTITY}
SIGNING_MODE=${SIGNING_MODE}
CONFIG
    exit 0
fi

cd "${REPO_ROOT}"

if [[ "${SIGNING_MODE}" == "ad-hoc" ]]; then
    echo "==> No Developer ID certificate found: ad-hoc signing."
    echo "    The bundle is UNSIGNED FOR DISTRIBUTION (local use only)."
else
    echo "==> Signing with Developer ID identity ${CODESIGN_IDENTITY}"
fi

echo "==> Building Swift shell (release)"
swift build -c release --package-path app/macos
SWIFT_BIN="$(swift build -c release --package-path app/macos --show-bin-path)/${APP_NAME}"

echo "==> Building harness wheel"
rm -f dist/local_code_bench-*.whl
uv build --wheel
WHEEL="$(ls dist/local_code_bench-*.whl)"

echo "==> Fetching relocatable CPython ${PBS_PYTHON} (${PBS_TAG})"
mkdir -p "${CACHE_DIR}"
if [[ ! -f "${CACHE_DIR}/${PBS_ARCHIVE}" ]]; then
    curl --fail --location --output "${CACHE_DIR}/${PBS_ARCHIVE}.tmp" "${PBS_URL}"
    mv "${CACHE_DIR}/${PBS_ARCHIVE}.tmp" "${CACHE_DIR}/${PBS_ARCHIVE}"
fi

echo "==> Verifying archive checksum"
if ! echo "${PBS_SHA256}  ${CACHE_DIR}/${PBS_ARCHIVE}" | shasum -a 256 --check --status; then
    echo "error: ${PBS_ARCHIVE} does not match pbs_sha256 in configs/build.yaml" >&2
    echo "       (delete ${CACHE_DIR}/${PBS_ARCHIVE} to re-download)" >&2
    exit 1
fi

echo "==> Assembling ${APP_DIR}"
rm -rf "${APP_DIR}"
mkdir -p "${APP_DIR}/Contents/MacOS" "${APP_DIR}/Contents/Resources"
cp "${SWIFT_BIN}" "${APP_DIR}/Contents/MacOS/${APP_NAME}"
# The archive extracts to python/ — exactly the layout BundledRuntime expects
# (Contents/Resources/python/bin/python3).
tar -xzf "${CACHE_DIR}/${PBS_ARCHIVE}" -C "${APP_DIR}/Contents/Resources"

cat > "${APP_DIR}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
    <key>CFBundleName</key><string>Local Code Bench</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${APP_VERSION}</string>
    <key>CFBundleVersion</key><string>${APP_VERSION}</string>
    <key>LSMinimumSystemVersion</key><string>${MIN_MACOS}</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

echo "==> Installing harness wheel into the bundled runtime"
BUNDLED_PYTHON="${APP_DIR}/Contents/Resources/python/bin/python3"
"${BUNDLED_PYTHON}" -m pip install --quiet --no-compile "${WHEEL}"

# Record the installed harness version for the app's about panel.
"${BUNDLED_PYTHON}" -c \
    "from importlib.metadata import version; print(version('local-code-bench'))" \
    > "${APP_DIR}/Contents/Resources/harness-version"

echo "==> Sanity check: python -m local_code_bench --help"
"${BUNDLED_PYTHON}" -m local_code_bench --help > /dev/null

echo "==> Codesigning inside-out (hardened runtime, ${SIGNING_MODE})"
# 1. Every nested shared library / extension module in the embedded runtime.
find "${APP_DIR}/Contents/Resources/python" -type f \
        \( -name '*.so' -o -name '*.dylib' \) -print0 \
    | xargs -0 codesign --force --options runtime --sign "${CODESIGN_IDENTITY}"
# 2. Mach-O executables in the runtime's bin/ (the real python binary; script
#    shims and symlinks are not code-signable and are skipped).
find "${APP_DIR}/Contents/Resources/python/bin" -type f -print0 \
    | while IFS= read -r -d '' binary; do
        if file -b "${binary}" | grep -q "Mach-O"; then
            codesign --force --options runtime \
                --entitlements "${ENTITLEMENTS}" \
                --sign "${CODESIGN_IDENTITY}" "${binary}"
        fi
    done
# 3. The bundle itself last: signs the main executable and seals the (already
#    signed) resources, so verification sees a consistent inside-out chain.
codesign --force --options runtime \
    --entitlements "${ENTITLEMENTS}" \
    --sign "${CODESIGN_IDENTITY}" "${APP_DIR}"

echo "==> Verifying signature"
codesign --verify --deep --strict --verbose=2 "${APP_DIR}"

echo "Built ${APP_DIR} (version ${APP_VERSION}, signing: ${SIGNING_MODE})"
if [[ "${SIGNING_MODE}" == "ad-hoc" ]]; then
    echo "NOTE: ad-hoc signature — unsigned for distribution, local use only."
fi
