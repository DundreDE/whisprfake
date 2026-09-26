#!/bin/bash
# Restart the daemon in the background (dev helper). Uses `sudo -g input` until you have re-logged in
# after being added to the input group; afterwards the systemd user service replaces this.
set -u
PIDFILE="$XDG_RUNTIME_DIR/whisprfake.pid"
LOG="${WHISPRFAKE_LOGFILE:-$XDG_RUNTIME_DIR/whisprfake.log}"
BIN="$(cd "$(dirname "$0")/.." && pwd)/.venv/bin/whisprfake"
if [[ -f $PIDFILE ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then sleep 1.5; fi
if id -nG | grep -qw input; then
  setsid nohup "$BIN" daemon >"$LOG" 2>&1 &
else
  sudo -n -u "$USER" -g input --preserve-env setsid nohup "$BIN" daemon >"$LOG" 2>&1 &
fi
echo "log: $LOG"
