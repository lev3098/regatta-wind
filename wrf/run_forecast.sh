#!/usr/bin/env bash
# Run the full WPS→WRF chain INSIDE the NCAR WRF container and copy wrfout to the
# mounted output dir. Driven by the namelist templates in /wrf/regatta.
#
# Env / args:
#   START      forecast start "YYYY-MM-DD_HH" (default: read from /wrf/gfs/CYCLE)
#   RUN_HOURS  default 12
#   MAX_DOM    2 (9→3 km, default) or 3 (adds the 1 km nest)
#   NP         MPI ranks (default 4 — matches a 4-core i5)
set -euo pipefail

WPS=/wrf/WPS
EMREAL=/wrf/WRF/test/em_real
GFS=/wrf/gfs
OUT=/wrf/wrfoutput
TPL=/wrf/regatta            # mounted: namelist.wps, namelist.input

RUN_HOURS="${RUN_HOURS:-12}"
MAX_DOM="${MAX_DOM:-2}"
NP="${NP:-4}"
START="${START:-$(cat "${GFS}/CYCLE" 2>/dev/null || true)}"
[[ -n "${START}" ]] || { echo "Не задан START и нет ${GFS}/CYCLE — запусти download_gfs.sh."; exit 1; }

SY=${START:0:4}; SM=${START:5:2}; SD=${START:8:2}; SH=${START:11:2}
START_FMT="${SY}-${SM}-${SD}_${SH}:00:00"
END_FMT=$(date -u -d "${SY}-${SM}-${SD} ${SH}:00:00 +${RUN_HOURS} hours" +%Y-%m-%d_%H:00:00)
EY=${END_FMT:0:4}; EM=${END_FMT:5:2}; ED=${END_FMT:8:2}; EH=${END_FMT:11:2}
echo "WRF run ${START_FMT} → ${END_FMT}  (MAX_DOM=${MAX_DOM}, NP=${NP})"

# ── fill namelist templates ─────────────────────────────────────────────────
fill_wps() {
  sed -e "s/@MAX_DOM@/${MAX_DOM}/g" \
      -e "s/@START_DATE@/${START_FMT}/g" \
      -e "s/@END_DATE@/${END_FMT}/g" "${TPL}/namelist.wps" > "${WPS}/namelist.wps"
}
fill_input() {
  sed -e "s/@MAX_DOM@/${MAX_DOM}/g" -e "s/@RUN_HOURS@/${RUN_HOURS}/g" \
      -e "s/@SY@/${SY}/g" -e "s/@SM@/${SM}/g" -e "s/@SD@/${SD}/g" -e "s/@SH@/${SH}/g" \
      -e "s/@EY@/${EY}/g" -e "s/@EM@/${EM}/g" -e "s/@ED@/${ED}/g" -e "s/@EH@/${EH}/g" \
      "${TPL}/namelist.input" > "${EMREAL}/namelist.input"
}
fill_wps
fill_input

# ── WPS ─────────────────────────────────────────────────────────────────────
cd "${WPS}"
echo "[1/5] geogrid"; ./geogrid.exe >/tmp/geogrid.log 2>&1
echo "[2/5] ungrib";  ./link_grib.csh "${GFS}"/gfs.* ; cp ungrib/Variable_Tables/Vtable.GFS Vtable ; ./ungrib.exe >/tmp/ungrib.log 2>&1
echo "[3/5] metgrid"; ./metgrid.exe >/tmp/metgrid.log 2>&1
ls met_em.d0*.nc >/dev/null || { echo "metgrid не создал met_em — смотри /tmp/metgrid.log"; exit 1; }

# ── WRF ─────────────────────────────────────────────────────────────────────
cd "${EMREAL}"
ln -sf "${WPS}"/met_em.d0*.nc .
echo "[4/5] real.exe"; mpirun -np "${NP}" ./real.exe >/tmp/real.log 2>&1
tail -1 rsl.error.0000 2>/dev/null | grep -q SUCCESS || { echo "real.exe FAILED — см. rsl.error.0000"; exit 1; }
echo "[5/5] wrf.exe (это самый долгий шаг — часы)"; mpirun -np "${NP}" ./wrf.exe >/tmp/wrf.log 2>&1
tail -1 rsl.error.0000 2>/dev/null | grep -q SUCCESS || { echo "wrf.exe FAILED — см. rsl.error.0000"; exit 1; }

mkdir -p "${OUT}"
cp -f wrfout_d0*  "${OUT}/"
echo "✅ Готово. Файлы в ${OUT}:"; ls -1 "${OUT}"/wrfout_d0*
