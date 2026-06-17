"""Export the forecast as an animated .mp4 (smooth gradient + arrows).

Each forecast hour occupies ``seconds_per_hour`` seconds; in between, the wind is
interpolated in vector (u, v) space so the gradient and arrows morph smoothly
instead of cutting. Pure Pillow rendering + imageio/ffmpeg encoding — no Streamlit
import, so it stays testable and engine-agnostic (any ``FineField``).
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .grid import speed_dir_to_uv, uv_to_speed_dir
from .models import FineField, Waypoint

# Same Windy-like ramp as the map (kept local so this module imports no UI code).
_STOPS = [
    (0.00, (40, 90, 160)), (0.15, (54, 160, 204)), (0.32, (90, 200, 170)),
    (0.50, (150, 210, 120)), (0.64, (235, 225, 110)), (0.78, (240, 165, 75)),
    (0.90, (224, 80, 60)), (1.00, (160, 40, 90)),
]
_BG = (12, 14, 18)
_LAND = (28, 32, 40)


def _speed_to_rgb(speed: np.ndarray, vmax: float) -> np.ndarray:
    pos = np.array([s[0] for s in _STOPS])
    rgb = np.array([s[1] for s in _STOPS], dtype=float)
    t = np.clip(np.nan_to_num(speed, nan=0.0) / max(vmax, 1e-3), 0.0, 1.0)
    r = np.interp(t, pos, rgb[:, 0])
    g = np.interp(t, pos, rgb[:, 1])
    b = np.interp(t, pos, rgb[:, 2])
    return np.dstack([r, g, b]).astype(np.uint8)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in ("/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/Library/Fonts/Arial.ttf",
                 "/System/Library/Fonts/Helvetica.ttc",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


class _Canvas:
    """Static layout + reusable pieces computed once for the whole video."""

    def __init__(self, field: FineField, corners: list[Waypoint], vmax: float,
                 width: int = 3840, height: int = 2160):
        self.W, self.H = width, height
        self.vmax = vmax
        s = height / 720.0  # scale every visual element relative to the 720p baseline
        self.s = s
        self.arrow_w = max(1, round(2 * s))
        self.dot_r = max(2, round(3 * s))
        lat, lon = field.terrain.lat, field.terrain.lon
        self.lat_lo, self.lat_hi = float(np.min(lat)), float(np.max(lat))
        self.lon_lo, self.lon_hi = float(np.min(lon)), float(np.max(lon))

        left, right, top, bot = round(18 * s), round(120 * s), round(52 * s), round(20 * s)
        aw, ah = self.W - left - right, self.H - top - bot
        clat = (self.lat_lo + self.lat_hi) / 2
        aspect = ((self.lon_hi - self.lon_lo) * math.cos(math.radians(clat))) / \
                 max(self.lat_hi - self.lat_lo, 1e-6)
        if aw / ah > aspect:
            fh = ah
            fw = int(ah * aspect)
        else:
            fw = aw
            fh = int(aw / aspect)
        self.fw, self.fh = max(fw, 2), max(fh, 2)
        self.fx = left + (aw - self.fw) // 2
        self.fy = top + (ah - self.fh) // 2

        # blur tied to one grid cell (not to resolution) so the gradient stays
        # crisp at 4K instead of mushy — just enough to melt cell facets.
        self.ny, self.nx = int(lat.shape[0]), int(lat.shape[1])
        self.cell_px = self.fw / max(self.nx - 1, 1)
        self.blur_px = max(0.6, self.cell_px * 0.32)

        # land dimming mask (north-up), resized to the field rect
        land = field.terrain.is_land
        if land is not None and np.any(land) and not np.all(land):
            m = (np.flipud(land.astype(np.uint8)) * 130).astype(np.uint8)
            self.land_mask = Image.fromarray(m, mode="L").resize((self.fw, self.fh), Image.BILINEAR)
            self.dark = Image.new("RGB", (self.fw, self.fh), _LAND)
        else:
            self.land_mask = None
            self.dark = None

        self.corner_px = [(w.name, self._px(w.lat, w.lon)) for w in (corners or [])]
        self.f_time = _font(round(26 * s))
        self.f_small = _font(round(15 * s))
        self.f_tick = _font(round(13 * s))
        self.cbw = max(8, round(18 * s))
        self.colorbar = self._colorbar()
        self.cb_x = self.W - right + round(22 * s)

    def _px(self, lat: float, lon: float) -> tuple[int, int]:
        x = self.fx + (lon - self.lon_lo) / (self.lon_hi - self.lon_lo) * self.fw
        y = self.fy + (self.lat_hi - lat) / (self.lat_hi - self.lat_lo) * self.fh
        return int(round(x)), int(round(y))

    def _colorbar(self) -> Image.Image:
        bw, bh = self.cbw, self.fh
        col = np.zeros((bh, bw, 3), dtype=np.uint8)
        speeds = np.linspace(self.vmax, 0, bh)  # top = vmax
        rgb = _speed_to_rgb(speeds.reshape(-1, 1), self.vmax)[:, 0, :]
        col[:, :, :] = rgb[:, None, :]
        return Image.fromarray(col, mode="RGB")


def _field_image(canvas: _Canvas, speed: np.ndarray) -> Image.Image:
    """Crisp smooth gradient for one (interpolated) frame, sized to the field rect.

    The speed field is upscaled in DATA space (bicubic) before colour-mapping, then
    given a light sub-cell blur — this keeps the gradient sharp at high resolution.
    """
    fill = float(np.nanmean(speed)) if np.isfinite(speed).any() else 0.0
    filled = np.where(np.isfinite(speed), speed, fill).astype(np.float32)
    sp = Image.fromarray(np.flipud(filled), mode="F").resize((canvas.fw, canvas.fh), Image.BICUBIC)
    rgb = _speed_to_rgb(np.asarray(sp), canvas.vmax)
    img = Image.fromarray(rgb, mode="RGB").filter(ImageFilter.GaussianBlur(radius=canvas.blur_px))
    if canvas.land_mask is not None:
        img = Image.composite(canvas.dark, img, canvas.land_mask)
    return img


def _draw_arrows(draw: ImageDraw.ImageDraw, canvas: _Canvas, field: FineField,
                 speed: np.ndarray, direction: np.ndarray, arrows: int) -> None:
    if arrows <= 0:
        return
    lat2d, lon2d = field.terrain.lat, field.terrain.lon
    ny, nx = lat2d.shape
    step = max(1, math.ceil(max(ny, nx) / arrows))
    unit = step * (canvas.fw / max(nx - 1, 1)) * 0.95
    for i in range(0, ny, step):
        for j in range(0, nx, step):
            spd = float(speed[i, j])
            if not np.isfinite(spd) or spd <= 0.5:
                continue
            x, y = canvas._px(float(lat2d[i, j]), float(lon2d[i, j]))
            length = min(spd / max(canvas.vmax, 1), 1.25) * unit
            th = math.radians((float(direction[i, j]) + 180) % 360)  # downwind
            dx, dy = math.sin(th), -math.cos(th)
            hx, hy = x + dx * length, y + dy * length
            draw.line([(x, y), (hx, hy)], fill=(255, 255, 255, 255), width=canvas.arrow_w)
            for da in (150, -150):
                ba = th + math.radians(da)
                draw.line([(hx, hy), (hx + math.sin(ba) * length * 0.36,
                                      hy - math.cos(ba) * length * 0.36)],
                          fill=(255, 255, 255, 255), width=canvas.arrow_w)


def _render_frame(canvas: _Canvas, field: FineField, speed: np.ndarray,
                  direction: np.ndarray, when: datetime, arrows: int,
                  source: str) -> np.ndarray:
    s = canvas.s
    im = Image.new("RGB", (canvas.W, canvas.H), _BG)
    im.paste(_field_image(canvas, speed), (canvas.fx, canvas.fy))
    draw = ImageDraw.Draw(im)
    draw.rectangle([canvas.fx, canvas.fy, canvas.fx + canvas.fw, canvas.fy + canvas.fh],
                   outline=(70, 78, 90), width=max(1, round(s)))
    _draw_arrows(draw, canvas, field, speed, direction, arrows)

    r = canvas.dot_r
    for name, (x, y) in canvas.corner_px:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255))
        draw.text((x + round(7 * s), y - round(9 * s)), name, font=canvas.f_small,
                  fill=(235, 235, 235))

    # colour legend
    im.paste(canvas.colorbar, (canvas.cb_x, canvas.fy))
    for frac in (0.0, 0.5, 1.0):
        yy = canvas.fy + int((1 - frac) * canvas.fh)
        draw.text((canvas.cb_x + canvas.cbw + round(6 * s), yy - round(8 * s)),
                  f"{canvas.vmax * frac:.0f}", font=canvas.f_tick, fill=(220, 220, 220))
    draw.text((canvas.cb_x, canvas.fy - round(22 * s)), "узлы", font=canvas.f_tick,
              fill=(220, 220, 220))

    draw.text((canvas.fx, round(12 * s)), when.strftime("%d.%m %H:%M"), font=canvas.f_time,
              fill=(255, 255, 255))
    draw.text((canvas.fx + round(220 * s), round(20 * s)), source, font=canvas.f_small,
              fill=(170, 178, 190))
    return np.asarray(im)


def _interp_specs(field: FineField, fps: int, seconds_per_hour: float):
    """List of (speed2d, dir2d, time) — interpolated frames for the whole clip."""
    times, frames = field.times, field.frames
    n = len(frames)
    nsub = max(1, int(round(fps * seconds_per_hour)))
    specs: list[tuple[np.ndarray, np.ndarray, datetime]] = []
    if n == 1:
        for _ in range(nsub):
            specs.append((frames[0].speed_kn, frames[0].dir_deg, times[0]))
        return specs
    for k in range(n - 1):
        u0, v0 = speed_dir_to_uv(frames[k].speed_kn, frames[k].dir_deg)
        u1, v1 = speed_dir_to_uv(frames[k + 1].speed_kn, frames[k + 1].dir_deg)
        dt = times[k + 1] - times[k]
        for s in range(nsub):
            f = s / nsub
            spd, dr = uv_to_speed_dir(u0 * (1 - f) + u1 * f, v0 * (1 - f) + v1 * f)
            specs.append((spd, dr, times[k] + dt * f))
    for _ in range(max(1, fps)):  # ~1 s hold on the last hour
        specs.append((frames[-1].speed_kn, frames[-1].dir_deg, times[-1]))
    return specs


def render_mp4(
    field: FineField,
    corners: list[Waypoint] | None = None,
    *,
    seconds_per_hour: float = 5.0,
    fps: int = 12,
    vmax: float = 30.0,
    arrows: int = 28,
    width: int = 3840,
    height: int = 2160,
    progress: Callable[[int, int], None] | None = None,
) -> bytes:
    """Render the forecast to an .mp4 and return its bytes (default 4K UHD)."""
    import imageio.v2 as imageio  # local import: optional dependency

    if not field.frames:
        raise ValueError("Нет кадров прогноза для видео.")

    canvas = _Canvas(field, corners or [], vmax, width=width, height=height)
    specs = _interp_specs(field, fps, seconds_per_hour)
    total = len(specs)

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    writer = imageio.get_writer(tmp.name, fps=fps, codec="libx264", quality=8,
                                macro_block_size=16, pixelformat="yuv420p")
    try:
        for k, (spd, dr, when) in enumerate(specs):
            writer.append_data(_render_frame(canvas, field, spd, dr, when, arrows, field.source))
            if progress:
                progress(k + 1, total)
    finally:
        writer.close()

    with open(tmp.name, "rb") as fh:
        data = fh.read()
    os.unlink(tmp.name)
    return data
