# Source before building or running WRF/WPS natively on macOS (Apple Silicon).
# Поправь WRF_ROOT / версии каталогов, если ставил в другое место.
export WRF_ROOT="${WRF_ROOT:-$HOME/wrf}"
export WRF_SRC="$WRF_ROOT/WRFV4.8.0"
export WPS_SRC="$WRF_ROOT/WPS-4.6.0"
export GEOG="$WRF_ROOT/WPS_GEOG"

# Слитый префикс netcdf (C+Fortran в одном месте — WRF этого требует).
export NETCDF="$WRF_ROOT/netcdf"
export JASPERLIB="$(brew --prefix jasper)/lib"
export JASPERINC="$(brew --prefix jasper)/include"
# gfortran/gcc из Homebrew (gcc-15) впереди системного clang.
export PATH="$(brew --prefix gcc)/bin:$PATH"

export WRF_EM_CORE=1
export WRF_NMM_CORE=0
export WRF_DA_CORE=0
