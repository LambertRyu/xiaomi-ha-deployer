#!/bin/sh

set -eu
SCRIPT_DIR=/data/plugin/www/u__NAS_UID__/homeassistant
if [ ! -d "$SCRIPT_DIR" ]; then
  SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fi
exec "$SCRIPT_DIR/homeassistant.cgi"
