#!/usr/bin/env bash
# Запуск веб-интерфейса regatta-wind (Streamlit).
# Использует .venv, если он есть; иначе системный python3.
#   ./run.sh                 # порт 8501 по умолчанию
#   ./run.sh --server.port 8600
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PY="${PYTHON:-$HERE/.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"

echo "⛵ regatta-wind → http://localhost:8501"
exec "$PY" -m streamlit run app.py "$@"
