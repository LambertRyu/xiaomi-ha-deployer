#!/bin/sh

# Static-only plugin entrypoint. The NAS plugin chain supplies FILE_URI and/or
# FILE_REQ after authenticating the request; this script never proxies HA.
set -eu

# Newer plugin.cgi launches child scripts using fexecve: $0 can be /dev/fd/3.
# Use the same registered UI path rather than the file descriptor directory.
ROOT=/data/plugin/www/u__NAS_UID__/homeassistant
if [ ! -d "$ROOT" ]; then
  ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fi
URI=${FILE_URI:-index.html}

case "$URI" in
  ""|/) URI=index.html ;;
  /*|*..*|*\\*)
    printf 'Status: 400 Bad Request\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nInvalid path.\n'
    exit 0
    ;;
esac

FILE=$ROOT/$URI
if [ -n "${FILE_REQ:-}" ]; then
  case "$FILE_REQ" in
    /*) FILE=$FILE_REQ ;;
    *)
      printf 'Status: 400 Bad Request\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nInvalid file path.\n'
      exit 0
      ;;
  esac
fi

# Resolve both paths before checking containment so an in-tree symlink or a
# lexical /../ path cannot escape the UI root.
REAL_ROOT=$(readlink -f "$ROOT" 2>/dev/null || true)
REAL_FILE=$(readlink -f "$FILE" 2>/dev/null || true)
[ -n "$REAL_ROOT" ] || {
  printf 'Status: 500 Internal Server Error\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nUnable to resolve UI root.\n'
  exit 0
}
[ -n "$REAL_FILE" ] || {
  printf 'Status: 404 Not Found\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nNot found.\n'
  exit 0
}
case "$REAL_FILE" in
  "$REAL_ROOT"/*) ;;
  *)
    printf 'Status: 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nForbidden.\n'
    exit 0
    ;;
esac
FILE=$REAL_FILE

if [ ! -f "$FILE" ]; then
  printf 'Status: 404 Not Found\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nNot found.\n'
  exit 0
fi

case "$FILE" in
  *.html) CONTENT_TYPE='text/html; charset=utf-8' ;;
  *.css) CONTENT_TYPE='text/css; charset=utf-8' ;;
  *.js) CONTENT_TYPE='application/javascript; charset=utf-8' ;;
  *.png) CONTENT_TYPE='image/png' ;;
  *.svg) CONTENT_TYPE='image/svg+xml' ;;
  *) CONTENT_TYPE='application/octet-stream' ;;
esac

printf 'Status: 200 OK\r\nContent-Type: %s\r\nCache-Control: no-store\r\n\r\n' "$CONTENT_TYPE"
cat "$FILE"
