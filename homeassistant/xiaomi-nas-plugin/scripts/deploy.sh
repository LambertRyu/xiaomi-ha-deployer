#!/bin/sh

# Deploy the unchanged shortcut and its dedicated recovery service. No Docker,
# HA, SSH, Nginx, or vendor ports configuration is changed.
set -eu

PLUGIN=homeassistant
NAS_UID=${NAS_UID:-__NAS_UID__}
NAS_ROOT=${NAS_ROOT:-/}
APPLY=0
REPLACE=0

usage() {
  echo "Usage: $0 [--dry-run] [--apply] [--replace]"
  echo "  NAS_ROOT=/path may be used for staging; default is /"
}

for arg in "$@"; do
  case "$arg" in
    --dry-run) APPLY=0 ;;
    --apply) APPLY=1 ;;
    --replace) REPLACE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

case "$NAS_UID" in
  ''|*[!0-9]*) echo "NAS_UID must be set to the numeric NAS user UID" >&2; exit 2 ;;
esac

SOURCE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# Recovery owns installation and serializes it with background repairs.
set -- install --uid "$NAS_UID" --source "$SOURCE_ROOT" --root "$NAS_ROOT"
[ "$REPLACE" -eq 0 ] || set -- "$@" --replace
[ "$APPLY" -eq 0 ] || set -- "$@" --apply
exec python3 "$SOURCE_ROOT/scripts/entry_recovery.py" "$@"
