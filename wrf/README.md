# Локальный WRF-прогноз

Батч считает высокоразрешающий прогноз ветра на **12 ч** для залива Петра Великого
**локально**, кладёт `wrfout_d02_*` в `output/`, приложение (`streamlit run app.py`)
подхватывает его автоматически. Домены **9→3 км**, опционально вложенный **1 км**.

Два пути: **macOS (Apple Silicon)** — нативная сборка (рекомендуется на M-чипах) и
**Windows** — Docker. Выбирай свой.

---

## macOS (Apple Silicon, M1/M2/M3) — нативно ✅ проверено на M3

WRF считается на **CPU** (видеокарта не используется — CUDA-версии боевого WRF нет),
но 8 ядер M3 тянут это бодро: 9→3 км на 12 ч — ориентировочно **~10–20 мин**.

### Разовая сборка

```bash
cd wrf
./build_macos.sh        # Homebrew-зависимости, WRF 4.8 + WPS 4.6, WPS_GEOG (~30–50 мин, один раз)
```

Скрипт ставит `gcc/gfortran`, `open-mpi`, `netcdf(-fortran)`, `jasper`, собирает WRF и WPS
в `~/wrf`, качает геоданные. Он уже учитывает все подводные камни Apple Silicon (см. ниже).

### Каждый прогноз

```bash
cd wrf
./download_gfs.sh             # граничные данные GFS (регион + часы 0–12), пара минут
./run_forecast_macos.sh       # 9→3 км; MAX_DOM=3 ./run_forecast_macos.sh — добавить 1 км
```

Готовый `wrfout_d02_*` лёг в `wrf/output/` → приложение покажет его на «Авто».

### Что было нетривиально при сборке (уже зашито в скрипты)

- **netcdf C и Fortran** в Homebrew — разные префиксы; WRF требует один → слитый `~/wrf/netcdf`.
- **gfortran 15** падает на несовпадении типов → флаг `-fallow-argument-mismatch`.
- **ungrib + libpng**: в Homebrew отдельный префикс → добавлены `-I/-L` libpng (иначе нет `png.h`).
- **jasper 4.x** убрал `jpc_decode` → патч `dec_jpeg2000.c` на публичный `jas_image_decode` + `jas_init`.
- Параллельная сборка WRF (`-j`) ловит гонку порядка модулей → скрипт добивает последовательно.

---

## Windows — Docker (альтернатива)

> На Apple Silicon Docker гонял бы x86-образ через эмуляцию (медленно) — там используй нативный путь выше.
> Docker оправдан на Windows/Intel.

### Разовая подготовка

1. Поставь **Docker Desktop** (включит WSL2). Проверь `docker run hello-world`.
2. Собери образ WRF (несёт WRF+WPS+WPS_GEOG):

   ```bash
   git clone https://github.com/NCAR/WRF_DOCKER.git && cd WRF_DOCKER
   docker build -t wrf_local --build-arg argname=tutorial .
   ```

### Каждый прогноз (из `wrf/`)

```bash
./download_gfs.sh
docker compose run --rm wrf           # 9→3 км; MAX_DOM=3 docker compose run --rm wrf — +1 км
```

---

## Подключение к приложению

`config/route.yaml`:

```yaml
forecast:
  source: wrf
wrf:
  output_dir: wrf/output
  domain: d02        # d02 = 3 км, d03 = 1 км
```

Затем `streamlit run app.py`. Источник «Авто» берёт свежий `wrfout`, иначе — фолбэк Open-Meteo.

---

## Файлы

| Файл | Что делает |
|---|---|
| `build_macos.sh` | Нативная сборка WRF+WPS+geog на Apple Silicon (разово) |
| `env_macos.sh` | Переменные окружения для сборки/запуска (NETCDF, JASPER, PATH) |
| `run_forecast_macos.sh` | Нативный прогон: geogrid→ungrib→metgrid→real→wrf → `output/` |
| `download_gfs.sh` | GFS 0.25° (регион + часы 0–12) через NOMADS-фильтр |
| `run_forecast.sh` + `docker-compose.yml` | То же, но внутри Docker (Windows) |
| `namelist.wps` / `namelist.input` | Домены 9→3→1 км, физика побережья, 12 ч, `WSPD10MAX` (порывы) |

---

## Если что-то ломается

- **`real.exe`: ERROR while reading namelist** — переменная не в той группе или не-ASCII в комментариях.
  namelist'ы здесь уже чистый ASCII; `nwp_diagnostics` стоит в `&time_control` (а не `&physics`).
- **`real.exe`: mismatch num_metgrid_levels** — впиши число из его лога в `num_metgrid_levels`
  (для GFS 0.25° это 34, уже стоит).
- **GFS-цикл 404** — ещё не выложен (задержка ~4 ч), возьми ранний: `./download_gfs.sh ГГГГММДД 00`.
- **В приложении одна строка времени** — `frames_per_outfile` должен быть большим (все часы в один файл);
  здесь стоит 1000.
- **Память/долго** — держи `MAX_DOM=2`, при нужде уменьши `e_we/e_sn` синхронно в обоих namelist'ах.
- Логи прогона: `/tmp/{geogrid,ungrib,metgrid,real,wrf_run}.log` и `rsl.error.0000` в `test/em_real`.
