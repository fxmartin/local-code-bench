#!/usr/bin/env bash
# Notarized distribution pipeline for dist/LocalCodeBench.app (Story 18.3-002):
# submits the Developer-ID-signed bundle to Apple via `xcrun notarytool`
# (keychain profile), staples the ticket, wraps the app in a drag-install DMG,
# notarizes and staples the DMG too, and verifies both with Gatekeeper's own
# assessment (`spctl --assess`) so a clean machine opens the download with a
# normal double-click. It also writes the release notes stating the bundled
# harness version and the detected-not-bundled externals.
#
# Usage:
#   scripts/distribute-macos-app.sh                 notarize + package dist/
#   scripts/distribute-macos-app.sh --print-config  print the resolved config, exit
#
# Prerequisites:
#   - scripts/build-macos-app.sh produced a Developer-ID-signed dist/<app>.app
#     (ad-hoc bundles are labeled unsigned-for-distribution and refused here).
#   - A notarytool keychain profile exists, created once with:
#       xcrun notarytool store-credentials <notary_profile> \
#           --apple-id <id> --team-id <team> --password <app-specific-password>
#
# Pins live in configs/build.yaml (Epic-15 principle); NOTARY_PROFILE and
# GITHUB_REPO env vars override them for one-off experiments. The script
# enforces release alignment before anything ships: the bundle's
# CFBundleShortVersionString, the bundled harness wheel, and pyproject.toml's
# PSR-managed version must all agree, so every published DMG corresponds to a
# tagged harness release (vX.Y.Z).
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
    if [[ -z "${value}" ]]; then
        echo "error: ${key} missing from ${BUILD_CONFIG}" >&2
        exit 1
    fi
    printf '%s' "${value}"
}

APP_NAME="$(require_config app_name)"
NOTARY_PROFILE="${NOTARY_PROFILE:-$(require_config notary_profile)}"
GITHUB_REPO="${GITHUB_REPO:-$(require_config github_repo)}"
PBS_PYTHON="$(require_config pbs_python)"
PBS_TAG="$(require_config pbs_tag)"

APP_DIR="${REPO_ROOT}/dist/${APP_NAME}.app"
APP_VERSION="$(cd "${REPO_ROOT}" && uv version --short)"
DMG_PATH="${REPO_ROOT}/dist/${APP_NAME}-${APP_VERSION}.dmg"
NOTES_PATH="${REPO_ROOT}/dist/RELEASE-NOTES-${APP_VERSION}.md"

if [[ "${1:-}" == "--print-config" ]]; then
    cat <<CONFIG
APP_NAME=${APP_NAME}
APP_VERSION=${APP_VERSION}
NOTARY_PROFILE=${NOTARY_PROFILE}
GITHUB_REPO=${GITHUB_REPO}
APP_DIR=${APP_DIR}
DMG_PATH=${DMG_PATH}
NOTES_PATH=${NOTES_PATH}
CONFIG
    exit 0
fi

cd "${REPO_ROOT}"

if [[ ! -d "${APP_DIR}" ]]; then
    echo "error: ${APP_DIR} not found — run scripts/build-macos-app.sh first" >&2
    exit 1
fi

# Refuse ad-hoc signatures outright: the build script labels them
# unsigned-for-distribution, and Apple will not notarize them anyway.
SIGNATURE_INFO="$(codesign --display --verbose=2 "${APP_DIR}" 2>&1)"
if grep -q "Signature=adhoc" <<<"${SIGNATURE_INFO}"; then
    echo "error: ${APP_DIR} is ad-hoc signed (unsigned for distribution)." >&2
    echo "       Rebuild with a Developer ID Application certificate in the" >&2
    echo "       keychain (or a codesign_identity pin in configs/build.yaml)." >&2
    exit 1
fi

# Release alignment: the published app must correspond to a tagged harness
# release. The bundle's version and the harness wheel installed inside it are
# both stamped from pyproject.toml at build time; if either disagrees with the
# current PSR-managed version, the bundle is stale — rebuild, don't ship it.
BUNDLE_VERSION="$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" \
    "${APP_DIR}/Contents/Info.plist")"
HARNESS_VERSION="$(cat "${APP_DIR}/Contents/Resources/harness-version")"
if [[ "${BUNDLE_VERSION}" != "${APP_VERSION}" || "${HARNESS_VERSION}" != "${APP_VERSION}" ]]; then
    echo "error: version mismatch — the published app must correspond to the" >&2
    echo "       tagged harness release v${APP_VERSION} (pyproject.toml, PSR)." >&2
    echo "       Bundle: ${BUNDLE_VERSION}, bundled harness: ${HARNESS_VERSION}." >&2
    echo "       Re-run scripts/build-macos-app.sh from the release commit." >&2
    exit 1
fi

# `notarytool submit --wait` can exit 0 for an Invalid submission, so gate on
# the Accepted status in its output, not the exit code.
notarize() {
    local artifact="$1" output
    echo "==> Notarizing $(basename "${artifact}") (profile: ${NOTARY_PROFILE})"
    output="$(xcrun notarytool submit "${artifact}" \
        --keychain-profile "${NOTARY_PROFILE}" --wait 2>&1 | tee /dev/stderr)"
    if ! grep -q "status: Accepted" <<<"${output}"; then
        echo "error: notarization of $(basename "${artifact}") was not accepted." >&2
        echo "       Inspect with: xcrun notarytool log <submission-id>" \
            "--keychain-profile ${NOTARY_PROFILE}" >&2
        exit 1
    fi
}

echo "==> Submitting the app bundle"
APP_ZIP="$(mktemp -d)/${APP_NAME}.zip"
ditto -c -k --keepParent "${APP_DIR}" "${APP_ZIP}"
notarize "${APP_ZIP}"
rm -f "${APP_ZIP}"

echo "==> Stapling the app"
xcrun stapler staple "${APP_DIR}"

echo "==> Building ${DMG_PATH}"
STAGING="$(mktemp -d)"
ditto "${APP_DIR}" "${STAGING}/${APP_NAME}.app"
ln -s /Applications "${STAGING}/Applications"
rm -f "${DMG_PATH}"
hdiutil create -volname "Local Code Bench" -srcfolder "${STAGING}" \
    -format UDZO -ov "${DMG_PATH}"
rm -rf "${STAGING}"

notarize "${DMG_PATH}"
echo "==> Stapling the DMG"
xcrun stapler staple "${DMG_PATH}"

echo "==> Gatekeeper assessment"
spctl --assess --type execute --verbose=2 "${APP_DIR}"
spctl --assess --type open --context context:primary-signature \
    --verbose=2 "${DMG_PATH}"

echo "==> Writing ${NOTES_PATH}"
cat > "${NOTES_PATH}" <<NOTES
# Local Code Bench.app ${APP_VERSION}

Corresponds to harness release tag \`v${APP_VERSION}\`
(https://github.com/${GITHUB_REPO}/releases/tag/v${APP_VERSION}).

- **Bundled harness**: \`local-code-bench\` ${HARNESS_VERSION} (the release
  wheel, installed into the embedded runtime at build time)
- **Bundled runtime**: relocatable CPython ${PBS_PYTHON}
  (python-build-standalone ${PBS_TAG}, checksum-verified)
- **Signed & notarized**: Developer ID, hardened runtime, notarized and
  stapled (app and DMG) — a clean machine opens it with a normal double-click.

## Detected, not bundled

The app ships only the harness and its Python runtime. Everything it
benchmarks or drives is *detected* on your machine, never installed or
bundled: inference engines (\`mlx_lm.server\`, \`ollama\`), agent CLIs
(\`codex\`), proxies, and \`uv\` (only needed for the checkout install path).

## Install

- **Download**: open \`$(basename "${DMG_PATH}")\`, drag Local Code Bench to
  Applications, double-click. Nothing else is required.
- **From a checkout**: \`uv run bench dashboard\` — functionally identical.
NOTES

echo "Distributable ready: ${DMG_PATH} (release v${APP_VERSION})"
echo "Release notes:       ${NOTES_PATH}"
