#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PYTHON="$ROOT_DIR/.venv/bin/python"
CONFIG="$ROOT_DIR/surveillance.config.json"
if [[ ! -x "$PYTHON" ]]; then
  echo "Environnement Python absent. Créez-le avec : python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Information-only options must not stop a running server.
if [[ "${1:-}" == "--help" || "${1:-}" == "--version" ]]; then
  exec "$PYTHON" surveillance.py "$@"
fi

mapfile -t running_pids < <("$PYTHON" - "$ROOT_DIR" <<'PY'
import glob
import os
import sys

root = os.path.realpath(sys.argv[1])
expected_script = os.path.join(root, "surveillance.py")
expected_config = os.path.join(root, "surveillance.config.json")
for entry in glob.glob("/proc/[0-9]*/cmdline"):
    pid = entry.split("/")[2]
    try:
        args = [part.decode(errors="surrogateescape") for part in open(entry, "rb").read().split(b"\0") if part]
        cwd = os.path.realpath(f"/proc/{pid}/cwd")
    except (OSError, ValueError):
        continue
    if len(args) < 2:
        continue
    script = args[1]
    script = os.path.realpath(script if os.path.isabs(script) else os.path.join(cwd, script))
    if script != expected_script:
        continue
    config = None
    for index, arg in enumerate(args[2:], start=2):
        if arg == "--config" and index + 1 < len(args):
            config = args[index + 1]
            break
        if arg.startswith("--config="):
            config = arg.split("=", 1)[1]
            break
    config = config or "surveillance.config.json"
    config = os.path.realpath(config if os.path.isabs(config) else os.path.join(cwd, config))
    if config == expected_config:
        print(pid)
PY
)

for pid in "${running_pids[@]}"; do
  [[ -n "$pid" ]] || continue
  echo "Arrêt de l’application existante (PID $pid)…"
  kill -TERM "$pid" 2>/dev/null || continue
  for _ in $(seq 1 150); do
    process_state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
    [[ -z "$process_state" || "$process_state" == Z* ]] && break
    sleep 0.2
  done
  process_state="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$process_state" && "$process_state" != Z* ]]; then
    echo "Arrêt forcé de l’application (PID $pid) après expiration du délai." >&2
    kill -KILL "$pid" 2>/dev/null || true
  fi
done

echo "Démarrage de l’application web…"
exec "$PYTHON" surveillance.py --config "$CONFIG" "$@"
