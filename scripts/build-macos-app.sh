#!/usr/bin/env bash
# Assemble dist/LocalCodeBench.app (Story 18.1-002): the SwiftUI shell plus a
# relocatable CPython (python-build-standalone) with the harness wheel and its
# dependencies installed into Contents/Resources/python, so the app runs on a
# Mac with no Python tooling at all.
#
# Usage: scripts/build-macos-app.sh
# Overridable pins: PBS_TAG / PBS_PYTHON (python-build-standalone release tag
# and CPython version). Requires: swift (Command Line Tools), uv, curl, network.
set -euo pipefail

PBS_TAG="${PBS_TAG:-20241016}"
PBS_PYTHON="${PBS_PYTHON:-3.12.7}"
PBS_ARCH="aarch64-apple-darwin"
PBS_ARCHIVE="cpython-${PBS_PYTHON}+${PBS_TAG}-${PBS_ARCH}-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_ARCHIVE}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="LocalCodeBench"
APP_DIR="${REPO_ROOT}/dist/${APP_NAME}.app"
CACHE_DIR="${REPO_ROOT}/.runtime/pbs-cache"

cd "${REPO_ROOT}"

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
    <key>CFBundleIdentifier</key><string>me.fxmartin.local-code-bench</string>
    <key>CFBundleName</key><string>Local Code Bench</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$(uv version --short)</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

echo "==> Installing harness wheel into the bundled runtime"
BUNDLED_PYTHON="${APP_DIR}/Contents/Resources/python/bin/python3"
"${BUNDLED_PYTHON}" -m pip install --quiet --no-compile "${WHEEL}"

echo "==> Sanity check: python -m local_code_bench --help"
"${BUNDLED_PYTHON}" -m local_code_bench --help > /dev/null

# Ad-hoc signature so Gatekeeper allows a locally built bundle to launch.
codesign --force --deep --sign - "${APP_DIR}"

echo "Built ${APP_DIR}"
