#!/bin/sh

# Deploy on the NAS as root. Default is a dry-run; --apply is mandatory for
# writes. This script intentionally does not touch Docker, HA, Nginx, or
# systemd.
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
TARGET_SRC=$NAS_ROOT/nas/pool0/u$NAS_UID/plugin/pluginsrc/$PLUGIN
TARGET_HOME=$NAS_ROOT/home/u$NAS_UID/plugin/$PLUGIN
TARGET_UI=$NAS_ROOT/data/plugin/www/u$NAS_UID/$PLUGIN
TARGET_ICON=$NAS_ROOT/data/plugin/www/icon/$PLUGIN.icon
LIST_PATH=$NAS_ROOT/data/plugin/u$NAS_UID.list
MERGER=$SOURCE_ROOT/scripts/merge_list.py
ENTRY=$SOURCE_ROOT/registration/homeassistant.entry.json

for path in "$SOURCE_ROOT/INFO" "$SOURCE_ROOT/ui/index.html" \
  "$SOURCE_ROOT/ui/homeassistant.cgi" "$SOURCE_ROOT/ui/plugin.cgi" \
  "$MERGER" "$ENTRY"; do
  [ -f "$path" ] || { echo "missing source file: $path" >&2; exit 1; }
done
[ -f "$LIST_PATH" ] || { echo "missing existing .list: $LIST_PATH" >&2; exit 1; }

# Validate the registration before changing any plugin files.
if [ "$REPLACE" -eq 1 ]; then
  python3 "$MERGER" --list "$LIST_PATH" --entry "$ENTRY" --replace
else
  python3 "$MERGER" --list "$LIST_PATH" --entry "$ENTRY"
fi

if [ "$APPLY" -eq 0 ]; then
  echo "dry-run: source=$SOURCE_ROOT"
  echo "dry-run: plugin source -> $TARGET_SRC"
  echo "dry-run: user plugin entry -> $TARGET_HOME"
  echo "dry-run: UI symlink -> $TARGET_UI"
  echo "dry-run: icon -> $TARGET_ICON"
  echo "dry-run: registration -> $LIST_PATH"
  exit 0
fi

[ "$(id -u)" -eq 0 ] || { echo "--apply must run as root" >&2; exit 1; }
NAS_USER=u$NAS_UID
id "$NAS_USER" >/dev/null 2>&1 || { echo "missing NAS user: $NAS_USER" >&2; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)-$$
BACKUP=$LIST_PATH.bak-$STAMP
cp -p "$LIST_PATH" "$BACKUP"
mkdir -p "$TARGET_SRC" "$NAS_ROOT/data/plugin/www/icon" \
  "$(dirname -- "$TARGET_UI")" "$(dirname -- "$TARGET_HOME")"

if [ -e "$TARGET_HOME" ] && [ ! -d "$TARGET_HOME" ]; then
  echo "refusing to replace non-directory user plugin path: $TARGET_HOME" >&2
  exit 1
fi
mkdir -p "$TARGET_HOME"

if [ -e "$TARGET_UI" ] && [ ! -L "$TARGET_UI" ]; then
  echo "refusing to replace non-symlink UI path: $TARGET_UI" >&2
  exit 1
fi
if [ -e "$TARGET_ICON" ] && [ ! -f "$TARGET_ICON" ]; then
  echo "refusing to replace non-file icon path: $TARGET_ICON" >&2
  exit 1
fi
if [ -e "$TARGET_SRC/ui" ] || [ -L "$TARGET_SRC/ui" ]; then
  if [ ! -d "$TARGET_SRC/ui" ] && [ ! -L "$TARGET_SRC/ui" ]; then
    echo "refusing to replace non-directory UI path: $TARGET_SRC/ui" >&2
    exit 1
  fi
  mv "$TARGET_SRC/ui" "$TARGET_SRC/ui.bak-$STAMP"
fi

UI_STAGE=$TARGET_SRC/ui.deploy-$STAMP
if [ -e "$UI_STAGE" ] || [ -L "$UI_STAGE" ]; then
  echo "refusing to reuse staging path: $UI_STAGE" >&2
  exit 1
fi
if [ -e "$TARGET_SRC/INFO" ]; then
  cp -p "$TARGET_SRC/INFO" "$TARGET_SRC/INFO.bak-$STAMP"
fi
cp -p "$SOURCE_ROOT/INFO" "$TARGET_SRC/INFO"
cp -R "$SOURCE_ROOT/ui" "$UI_STAGE"
chmod 755 "$TARGET_SRC" "$UI_STAGE"
chmod 644 "$UI_STAGE/index.html" "$UI_STAGE/icon.png"
chmod 755 "$UI_STAGE/homeassistant.cgi"
chmod 755 "$UI_STAGE/plugin.cgi"
mv "$UI_STAGE" "$TARGET_SRC/ui"
chown -R "$NAS_USER:$NAS_USER" "$TARGET_SRC"

if [ -e "$TARGET_HOME/INFO" ]; then
  cp -p "$TARGET_HOME/INFO" "$TARGET_HOME/INFO.bak-$STAMP"
fi
cp -p "$SOURCE_ROOT/INFO" "$TARGET_HOME/INFO"
chmod 700 "$TARGET_HOME"
chmod 644 "$TARGET_HOME/INFO"
chown "$NAS_USER:$NAS_USER" "$TARGET_HOME" "$TARGET_HOME/INFO"
if [ -e "$TARGET_HOME/src" ] || [ -L "$TARGET_HOME/src" ]; then
  if [ ! -L "$TARGET_HOME/src" ]; then
    echo "refusing to replace non-symlink user plugin source: $TARGET_HOME/src" >&2
    exit 1
  fi
  mv "$TARGET_HOME/src" "$TARGET_HOME/src.bak-$STAMP"
fi
ln -s "$TARGET_SRC" "$TARGET_HOME/src"
chown -h "$NAS_USER:$NAS_USER" "$TARGET_HOME/src" 2>/dev/null || true

if [ -e "$TARGET_UI" ] || [ -L "$TARGET_UI" ]; then
  mv "$TARGET_UI" "$TARGET_UI.bak-$STAMP"
fi
ln -s "$TARGET_HOME/src/ui" "$TARGET_UI"
if [ -e "$TARGET_ICON" ]; then
  cp -p "$TARGET_ICON" "$TARGET_ICON.bak-$STAMP"
fi
cp -p "$TARGET_SRC/ui/icon.png" "$TARGET_ICON"

if [ "$REPLACE" -eq 1 ]; then
  python3 "$MERGER" --list "$LIST_PATH" --entry "$ENTRY" --replace --apply
else
  python3 "$MERGER" --list "$LIST_PATH" --entry "$ENTRY" --apply
fi
echo "deployed: $PLUGIN"
echo "backup: $BACKUP"
