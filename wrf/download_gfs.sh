#!/usr/bin/env bash
# Download GFS 0.25° boundary data for a 12 h forecast, subset to the Sea of Japan
# region (keeps files small). Runs on the HOST (needs internet); writes into ./gfs.
#
# Usage:
#   ./download_gfs.sh                 # auto-pick the latest safely-available cycle
#   ./download_gfs.sh 20260613 00     # explicit YYYYMMDD and cycle hour (00/06/12/18)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GFS_DIR="${HERE}/gfs"
FCST_HOURS=12
STEP=3                       # boundary interval (h) — matches interval_seconds=10800

# Region around Peter the Great Bay (with margin for the 9 km parent domain).
LEFT=124; RIGHT=141; TOP=49; BOTTOM=37

if [[ $# -ge 2 ]]; then
  YMD="$1"; CYC="$2"
else
  # GFS is published ~4 h after the cycle; step back 6 h and floor to 6-hourly.
  BASE=$(date -u -d "6 hours ago" +%s 2>/dev/null || date -u -v-6H +%s)
  YMD=$(date -u -d "@${BASE}" +%Y%m%d 2>/dev/null || date -u -r "${BASE}" +%Y%m%d)
  HH=$(date -u -d "@${BASE}" +%H 2>/dev/null || date -u -r "${BASE}" +%H)
  CYC=$(printf "%02d" $(( (10#$HH / 6) * 6 )))
fi

echo "GFS cycle: ${YMD} ${CYC}Z → ${FCST_HOURS} h, every ${STEP} h, region ${LEFT}..${RIGHT}E / ${BOTTOM}..${TOP}N"
mkdir -p "${GFS_DIR}"

BASE_URL="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
for (( f=0; f<=FCST_HOURS; f+=STEP )); do
  FFF=$(printf "%03d" "$f")
  OUT="${GFS_DIR}/gfs.t${CYC}z.pgrb2.0p25.f${FFF}"
  URL="${BASE_URL}?file=gfs.t${CYC}z.pgrb2.0p25.f${FFF}&all_lev=on&all_var=on&subregion=&leftlon=${LEFT}&rightlon=${RIGHT}&toplat=${TOP}&bottomlat=${BOTTOM}&dir=%2Fgfs.${YMD}%2F${CYC}%2Fatmos"
  echo "  ↓ f${FFF}"
  curl -fsS -o "${OUT}" "${URL}" || { echo "FAILED f${FFF} — цикл ещё не выложен? попробуй более ранний."; exit 1; }
done

# Record the cycle start for run_forecast.sh.
echo "${YMD:0:4}-${YMD:4:2}-${YMD:6:2}_${CYC}" > "${GFS_DIR}/CYCLE"
echo "Готово. Стартовое время: $(cat "${GFS_DIR}/CYCLE")  ($(ls "${GFS_DIR}"/gfs.* | wc -l | tr -d ' ') файлов)"
