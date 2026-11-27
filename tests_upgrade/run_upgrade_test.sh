#!/usr/bin/env bash
# Upgrade test: the previous release, then the current code on its registry.
#
#   OLD_REF=upstream/main tests_upgrade/run_upgrade_test.sh
#
# Needs pytest-homeassistant-custom-component in the active Python.
# OLD_REF is the git ref of the release users upgrade from (default:
# upstream/main, i.e. SEspe/xcomfort; in CI the base branch of the PR).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OLD_REF="${OLD_REF:-upstream/main}"
PYTHON="${PYTHON:-python}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export XC_UPGRADE_SNAPSHOT="$WORK/snapshot.json"

mkdir -p "$WORK/old"
git -C "$REPO" archive "$OLD_REF" custom_components/xcomfort | tar -x -C "$WORK/old"
PYTEST=("$PYTHON" -m pytest -q -s -p no:cacheprovider -c "$REPO/tests_upgrade/pytest.ini" --rootdir "$REPO/tests_upgrade")

for scenario in single pair; do
    export XC_SCENARIO="$scenario"
    echo "== [$scenario] old: $OLD_REF ($(git -C "$REPO" rev-parse --short "$OLD_REF"))"
    # Python puts the working directory first on sys.path with "-m", and the
    # test registers custom_components from there, so each phase runs the code
    # of the directory it runs in.
    (cd "$WORK/old" && "${PYTEST[@]}" "$REPO/tests_upgrade/test_upgrade.py::test_phase_old")
    echo "== [$scenario] new: working tree ($(git -C "$REPO" rev-parse --short HEAD))"
    (cd "$REPO" && "${PYTEST[@]}" "$REPO/tests_upgrade/test_upgrade.py::test_phase_new")
done