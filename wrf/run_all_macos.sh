#!/usr/bin/env bash
# One-click forecast: download GFS + run WRF, with a status file the UI polls.
# Launched (detached) by the "Просчитать прогноз" button, or run by hand.
#
# Env (optional): CENTER_LAT, CENTER_LON (recenter domain), RUN_HOURS, MAX_DOM, NP.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUS="$HERE/.compute_status"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "RUNNING $(stamp) подготовка" > "$STATUS"

echo "===== download_gfs $(date +%T) ====="
if ! "$HERE/download_gfs.sh"; then
  echo "FAILED $(stamp) не скачался GFS (цикл ещё не выложен?)" > "$STATUS"; exit 1
fi

echo "===== run_forecast $(date +%T) ====="
if ! "$HERE/run_forecast_macos.sh"; then
  echo "FAILED $(stamp) ошибка WRF (см. лог)" > "$STATUS"; exit 1
fi

echo "DONE $(stamp)" > "$STATUS"
echo "===== всё готово $(date +%T) ====="
