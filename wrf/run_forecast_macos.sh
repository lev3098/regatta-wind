#!/usr/bin/env bash
# Native WPS→WRF run on macOS (Apple Silicon) — no Docker.
# Prereqs: WRF/WPS built (build_macos.sh), WPS_GEOG downloaded, GFS fetched
# (./download_gfs.sh). Produces wrfout in ./output where the Streamlit app reads it.
#
# Env / args:
#   START      "YYYY-MM-DD_HH" (default: ./gfs/CYCLE from download_gfs.sh)
#   RUN_HOURS  default 12
#   MAX_DOM    2 (9→3 km, default) | 3 (adds 1 km nest)
#   NP         MPI ranks (default 6; M3 has 8 cores)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # repo/wrf
source "$HERE/env_macos.sh"

RUN_HOURS="${RUN_HOURS:-12}"
MAX_DOM="${MAX_DOM:-2}"
NP="${NP:-6}"
GFS_DIR="${GFS_DIR:-$HERE/gfs}"
OUT="${OUT:-$HERE/output}"
EMREAL="$WRF_SRC/test/em_real"

[[ -x "$EMREAL/real.exe" || -L "$EMREAL/real.exe" ]] || { echo "WRF не собран ($EMREAL/real.exe нет). Сначала build_macos.sh."; exit 1; }
[[ -d "$GEOG" ]] || { echo "Нет WPS_GEOG ($GEOG)."; exit 1; }

START="${START:-$(cat "$GFS_DIR/CYCLE" 2>/dev/null || true)}"
[[ -n "$START" ]] || { echo "Нет START и нет $GFS_DIR/CYCLE — запусти ./download_gfs.sh."; exit 1; }
SY=${START:0:4}; SM=${START:5:2}; SD=${START:8:2}; SH=${START:11:2}
START_FMT="${SY}-${SM}-${SD}_${SH}:00:00"
# END = START + RUN_HOURS (GNU date, иначе BSD date на macOS)
END_FMT=$(date -u -d "${SY}-${SM}-${SD} ${SH}:00:00 +${RUN_HOURS} hours" +%Y-%m-%d_%H:00:00 2>/dev/null \
        || date -u -j -v+"${RUN_HOURS}"H -f "%Y-%m-%d %H:%M:%S" "${SY}-${SM}-${SD} ${SH}:00:00" +%Y-%m-%d_%H:00:00)
EY=${END_FMT:0:4}; EM=${END_FMT:5:2}; ED=${END_FMT:8:2}; EH=${END_FMT:11:2}
echo "Прогноз $START_FMT → $END_FMT  (MAX_DOM=$MAX_DOM, NP=$NP)"

# ── namelists из шаблонов repo/wrf ──────────────────────────────────────────
sed -e "s/@MAX_DOM@/$MAX_DOM/g" -e "s/@START_DATE@/$START_FMT/g" -e "s/@END_DATE@/$END_FMT/g" \
    "$HERE/namelist.wps" > "$WPS_SRC/namelist.wps"
sed -i '' "s|geog_data_path = .*|geog_data_path = '$GEOG',|" "$WPS_SRC/namelist.wps"
# Optional: recenter the WRF domain on a chosen point (forces a geogrid rebuild).
if [[ -n "${CENTER_LAT:-}" && -n "${CENTER_LON:-}" ]]; then
  echo "Центр домена → ${CENTER_LAT}, ${CENTER_LON}"
  sed -i '' "s/ref_lat .*/ref_lat   = ${CENTER_LAT},/"   "$WPS_SRC/namelist.wps"
  sed -i '' "s/ref_lon .*/ref_lon   = ${CENTER_LON},/"   "$WPS_SRC/namelist.wps"
  sed -i '' "s/stand_lon .*/stand_lon = ${CENTER_LON},/" "$WPS_SRC/namelist.wps"
  rm -f "$WPS_SRC"/geo_em.d0*.nc
fi
sed -e "s/@MAX_DOM@/$MAX_DOM/g" -e "s/@RUN_HOURS@/$RUN_HOURS/g" \
    -e "s/@SY@/$SY/g" -e "s/@SM@/$SM/g" -e "s/@SD@/$SD/g" -e "s/@SH@/$SH/g" \
    -e "s/@EY@/$EY/g" -e "s/@EM@/$EM/g" -e "s/@ED@/$ED/g" -e "s/@EH@/$EH/g" \
    "$HERE/namelist.input" > "$EMREAL/namelist.input"

# ── WPS ─────────────────────────────────────────────────────────────────────
cd "$WPS_SRC"
# geo_em зависит только от конфигурации доменов (рельеф статичен) → переиспользуем
if ls geo_em.d01.nc >/dev/null 2>&1; then
  echo "[1/5] geogrid (пропуск — geo_em уже есть)"
else
  echo "[1/5] geogrid"; ./geogrid.exe > /tmp/geogrid.log 2>&1
fi
echo "[2/5] ungrib";  ./link_grib.csh "$GFS_DIR"/gfs.* ; ln -sf ungrib/Variable_Tables/Vtable.GFS Vtable ; ./ungrib.exe > /tmp/ungrib.log 2>&1
echo "[3/5] metgrid"; ./metgrid.exe > /tmp/metgrid.log 2>&1
ls met_em.d01.*.nc >/dev/null 2>&1 || { echo "metgrid не создал met_em — /tmp/metgrid.log"; tail -15 /tmp/metgrid.log; exit 1; }

# ── WRF ─────────────────────────────────────────────────────────────────────
cd "$EMREAL"
rm -f met_em.d0*.nc; ln -sf "$WPS_SRC"/met_em.d0*.nc .
echo "[4/5] real.exe"; mpirun -np "$NP" ./real.exe > /tmp/real.log 2>&1 || true
grep -q SUCCESS rsl.error.0000 2>/dev/null || { echo "real.exe FAILED — rsl.error.0000:"; tail -20 rsl.error.0000 2>/dev/null; exit 1; }
echo "[5/5] wrf.exe (самый долгий шаг)"; mpirun -np "$NP" ./wrf.exe > /tmp/wrf_run.log 2>&1 || true
grep -q SUCCESS rsl.error.0000 2>/dev/null || { echo "wrf.exe FAILED — rsl.error.0000:"; tail -20 rsl.error.0000 2>/dev/null; exit 1; }

mkdir -p "$OUT"; cp -f wrfout_d0* "$OUT/"
echo "✅ Готово. Файлы в $OUT:"; ls -1 "$OUT"/wrfout_d0*
