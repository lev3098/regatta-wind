"""Forecast sources. Each returns a unified :class:`regatta_wind.models.FineField`.

* :mod:`regatta_wind.sources.wrf` — reads local WRF ``wrfout`` netCDF (primary).
* :mod:`regatta_wind.sources.openmeteo` — coarse online fallback until WRF has run.
"""
