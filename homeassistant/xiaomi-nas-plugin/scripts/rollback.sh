#!/bin/sh

# Roll back only the .list registration. A backup path must be supplied and
# --apply is mandatory; plugin files are left in place for manual inspection.
set -eu

NAS_ROOT=${NAS_ROOT:-/}
NAS_UID=${NAS_UID:-__NAS_UID__}
LIST_PATH=$NAS_ROOT/data/plugin/u$NAS_UID.list
BACKUP=
APPLY=0

case "$NAS_UID" in
  ''|*[!0-9]*) echo "NAS_UID must be set to the numeric NAS user UID" >&2; exit 2 ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --backup) [ "$#" -ge 2 ] || { echo "--backup needs a path" >&2; exit 2; }; BACKUP=$2; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) echo "Usage: $0 --backup /path/to/u$NAS_UID.list.bak-TIMESTAMP --apply"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$BACKUP" ] || { echo "an explicit --backup path is required" >&2; exit 2; }
[ -f "$BACKUP" ] || { echo "backup not found: $BACKUP" >&2; exit 1; }
[ -f "$LIST_PATH" ] || { echo "current .list not found: $LIST_PATH" >&2; exit 1; }

if [ "$APPLY" -eq 0 ]; then
  echo "dry-run: would restore $LIST_PATH from $BACKUP"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { echo "--apply must run as root" >&2; exit 1; }
CURRENT_BACKUP=$LIST_PATH.rollback-$(date +%Y%m%d-%H%M%S)
cp -p "$LIST_PATH" "$CURRENT_BACKUP"
cp -p "$BACKUP" "$LIST_PATH"
echo "restored: $LIST_PATH"
echo "safety backup: $CURRENT_BACKUP"
