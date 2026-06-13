#!/usr/bin/env bash
# One-shot native build of WRF + WPS on macOS (Apple Silicon, M-series).
# Heavy & one-time: installs Homebrew deps, downloads ~3 GB, compiles (~30–50 мин).
# Re-running is safe — it reuses what's already built/downloaded.
#
# Result: ~/wrf/WRFV4.8.0/main/{real,wrf}.exe, ~/wrf/WPS-4.6.0/{geogrid,ungrib,metgrid}.exe,
#         ~/wrf/WPS_GEOG. Then: ./download_gfs.sh && ./run_forecast_macos.sh
set -euo pipefail

WRF_ROOT="${WRF_ROOT:-$HOME/wrf}"
WRF_VER=4.8.0
WPS_VER=4.6.0
mkdir -p "$WRF_ROOT"; cd "$WRF_ROOT"

echo "== 1/6 Homebrew-зависимости =="
brew install gcc open-mpi netcdf netcdf-fortran libpng jasper wget || true

echo "== 2/6 слитый netcdf-префикс (WRF требует C+Fortran вместе) =="
mkdir -p netcdf/include netcdf/lib netcdf/bin
for P in "$(brew --prefix netcdf)" "$(brew --prefix netcdf-fortran)"; do
  ln -sf "$P"/include/* netcdf/include/ 2>/dev/null || true
  ln -sf "$P"/lib/lib*  netcdf/lib/     2>/dev/null || true
  ln -sf "$P"/bin/*     netcdf/bin/     2>/dev/null || true
done

echo "== 3/6 исходники WRF $WRF_VER + WPS $WPS_VER =="
[ -d "WRFV${WRF_VER}" ] || { curl -fL --retry 3 -o wrf.tar.gz \
  "https://github.com/wrf-model/WRF/releases/download/v${WRF_VER}/v${WRF_VER}.tar.gz"; tar xzf wrf.tar.gz; }
[ -d "WPS-${WPS_VER}" ] || { curl -fL --retry 3 -o wps.tar.gz \
  "https://github.com/wrf-model/WPS/archive/refs/tags/v${WPS_VER}.tar.gz"; tar xzf wps.tar.gz; }

export NETCDF="$WRF_ROOT/netcdf"
export JASPERLIB="$(brew --prefix jasper)/lib"
export JASPERINC="$(brew --prefix jasper)/include"
export PATH="$(brew --prefix gcc)/bin:$PATH"      # gfortran/gcc-15 перед clang
export WRF_EM_CORE=1 WRF_NMM_CORE=0 WRF_DA_CORE=0

echo "== 4/6 сборка WRF (opt 35: gfortran/gcc + OpenMPI, dmpar) =="
cd "$WRF_ROOT/WRFV${WRF_VER}"
if [ ! -f main/wrf.exe ]; then
  printf '35\n1\n' | ./configure
  # gfortran 10+ иначе падает на несовпадении типов:
  sed -i '' '/^FCBASEOPTS_NO_G/ s/$/ -fallow-argument-mismatch -fallow-invalid-boz/' configure.wrf
  # -j4 быстро, затем последовательные дозапуски добивают гонку порядка модулей в phys/
  J="-j 4" ./compile em_real > /tmp/wrf_build.log 2>&1 || true
  for i in 1 2 3; do
    [ -f main/wrf.exe ] && break
    echo "   ...дозапуск $i (последовательно)"; J="-j 1" ./compile em_real >> /tmp/wrf_build.log 2>&1 || true
  done
fi
[ -f main/wrf.exe ] || { echo "❌ WRF не собрался — см. /tmp/wrf_build.log"; exit 1; }
echo "   ✅ WRF: $(ls main/*.exe | wc -l | tr -d ' ') исполняемых"

echo "== 5/6 сборка WPS (opt 17: Darwin gfortran/gcc serial + GRIB2) =="
cd "$WRF_ROOT/WPS-${WPS_VER}"
export WRF_DIR="$WRF_ROOT/WRFV${WRF_VER}"
if [ ! -f ungrib.exe ]; then
  ./configure <<< "17"
  sed -i '' '/^FFLAGS/ s/$/ -fallow-argument-mismatch -fallow-invalid-boz/' configure.wps
  # GRIB2 (ungrib) нужен libpng — в Homebrew это отдельный префикс, добавляем include+lib,
  # иначе dec_png.c не находит png.h и ungrib.exe не линкуется.
  JINC="$(brew --prefix jasper)/include"; JLIB="$(brew --prefix jasper)/lib"
  PINC="$(brew --prefix libpng)/include"; PLIB="$(brew --prefix libpng)/lib"
  sed -i '' "s#^COMPRESSION_INC.*#COMPRESSION_INC     = -I${JINC} -I${PINC}#" configure.wps
  sed -i '' "s#^COMPRESSION_LIBS.*#COMPRESSION_LIBS    = -L${JLIB} -L${PLIB} -ljasper -lpng -lz#" configure.wps
  # jasper ≥3 убрал внутренний jpc_decode → публичный API + инициализация (иначе ungrib не линкуется)
  DEC=ungrib/src/ngl/g2/dec_jpeg2000.c
  sed -i '' 's#^/\*    jas_init(); \*/#    { static int jas_inited = 0; if (!jas_inited) { jas_init(); jas_inited = 1; } }#' "$DEC"
  sed -i '' 's#image=jpc_decode(jpcstream,opts);#image=jas_image_decode(jpcstream,jas_image_strtofmt((char*)"jpc"),opts);#' "$DEC"
  ./compile > /tmp/wps_build.log 2>&1 || true
fi
{ [ -f geogrid.exe ] && [ -f ungrib.exe ] && [ -f metgrid.exe ]; } \
  || { echo "❌ WPS не собрался — см. /tmp/wps_build.log"; exit 1; }
echo "   ✅ WPS: geogrid/ungrib/metgrid"

echo "== 6/6 WPS_GEOG (рельеф/ландшафт, ~3 ГБ → ~29 ГБ) =="
if [ ! -d "$WRF_ROOT/WPS_GEOG" ]; then
  cd "$WRF_ROOT"
  curl -fL --retry 3 -o geog.tar.gz https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz
  tar xzf geog.tar.gz
fi
echo ""
echo "✅ Сборка завершена. Дальше из папки wrf/ репозитория:"
echo "   ./download_gfs.sh           # граничные данные GFS"
echo "   ./run_forecast_macos.sh     # прогноз (9→3 км); MAX_DOM=3 — добавить 1 км"
