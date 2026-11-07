"""Efficient Full HD movie frames rendered from existing simulation records.

Only presentation is handled here. The simulation, controller and safety logic
remain in their existing modules; this renderer never invents temperatures or
power. Pillow is required for export, not for core simulation.
"""
from __future__ import annotations

import bisect
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .environment import ambient_temperature
from .models import Config, PANELS, PanelId


BG = "#08111e"
CARD = "#101e31"
BORDER = "#263951"
TEXT = "#edf4ff"
MUTED = "#a4b8d2"
CYAN = "#48ded6"
ORANGE = "#ffad58"
BLUE = "#83acff"


@lru_cache(maxsize=40)
def _font(size: int, bold: bool = False):
    filename = "malgunbd.ttf" if bold else "malgun.ttf"
    path = Path("C:/Windows/Fonts") / filename
    if not path.exists():
        path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size)


def _text(draw, xy, text, size=24, fill=TEXT, bold=False, anchor=None):
    draw.text(xy, str(text), font=_font(size, bold), fill=fill, anchor=anchor)


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _blend(a, b, fraction):
    aa, bb = _rgb(a), _rgb(b)
    return tuple(round(x + (y - x) * fraction) for x, y in zip(aa, bb))


def _clock(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _dashed(draw, start, end, fill, width=2, dash=10):
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        return
    for offset in range(0, int(length), dash * 2):
        last = min(offset + dash, length)
        draw.line((x1 + (x2-x1)*offset/length, y1 + (y2-y1)*offset/length,
                   x1 + (x2-x1)*last/length, y1 + (y2-y1)*last/length), fill=fill, width=width)


# Each chart tuple is: card bounds, plot bounds, title, y-min, y-max, line colour.
def _chart_specs(config):
    return [
        ((1080, 322, 1864, 506), (1140, 378, 1837, 466), "등판 온도  /  °C", 20.0, 50.0, CYAN),
        ((1080, 520, 1864, 704), (1140, 576, 1837, 664), "외기 온도  /  °C", -6.0, 6.0, BLUE),
        ((1080, 718, 1864, 902), (1140, 774, 1837, 862), "실제 히터 전력  /  W", 0.0,
         max(30.0, config.heater_max_avg_power_w * 1.2), ORANGE),
    ]


def _point(t, value, plot, ymin, ymax, duration):
    x1, y1, x2, y2 = plot
    xx = x1 + min(duration, max(0, t)) / duration * (x2 - x1)
    yy = y2 - (min(ymax, max(ymin, value)) - ymin) / (ymax - ymin) * (y2 - y1)
    return xx, yy


@lru_cache(maxsize=8)
def _static_base(target: float, budget: float, duration: float, ambient_profile: str):
    image = Image.new("RGB", (1920, 1080), BG)
    draw = ImageDraw.Draw(image)
    _text(draw, (56, 26), "THERMAL LINK", 24, CYAN, True)
    _text(draw, (56, 62), "발열자켓 신경망 제어 시뮬레이션", 43, TEXT, True)
    profile = "외기 -5°C ↔ +5°C · 3분마다 선형 왕복" if ambient_profile == "triangle" else "고정 외기 온도"
    _text(draw, (58, 119), f"신경망 · PI 모방학습   |   {profile}", 23, MUTED)
    draw.rounded_rectangle((1554, 36, 1864, 122), radius=18, fill="#153c47", outline="#2d727a", width=2)
    _text(draw, (1580, 45), "10× 재생", 31, CYAN, True)
    _text(draw, (1580, 87), "가상 10분 = 영상 1분", 19, TEXT)

    labels = ("현재 등판 온도", "현재 외기 온도", "현재 히터 전력", "누적 히터 에너지")
    for i, label in enumerate(labels):
        x = 56 + i * 457
        draw.rounded_rectangle((x, 168, x + 437, 296), radius=18, fill=CARD, outline=BORDER, width=2)
        _text(draw, (x + 23, 183), label, 22, MUTED)

    draw.rounded_rectangle((56, 322, 1056, 902), radius=20, fill=CARD, outline=BORDER, width=2)
    _text(draw, (80, 340), "5개 발열패드 · 공통 PWM 1채널", 23, TEXT, True)
    _text(draw, (323, 391), "앞면 · 가슴 / 장갑", 20, MUTED, anchor="mt")
    _text(draw, (773, 391), "뒷면 · 등판", 20, MUTED, anchor="mt")

    # Jacket shapes share the same layout as the in-app visualisation.
    ox, oy, s = 83, 378, 1.36
    front = [(137,69),(111,79),(62,119),(29,247),(78,260),(108,160),(108,308),
             (250,308),(250,160),(280,260),(330,247),(296,119),(247,79),(220,69),(205,90),(153,90)]
    back = [(463,69),(438,79),(387,119),(371,232),(415,241),(434,158),(434,308),
            (575,308),(575,158),(596,241),(638,232),(622,119),(572,79),(548,69),(531,91),(479,91)]
    def tr(points):
        return [(ox+x*s, oy+y*s) for x,y in points]
    for points in (front, back):
        draw.polygon(tr(points), fill="#24364f", outline="#62758e", width=2)
    for center in (179, 505):
        draw.ellipse((ox+(center-42)*s, oy+39*s, ox+(center+42)*s, oy+98*s),
                     fill="#1b2b42", outline="#60748d", width=2)
        draw.line((ox+(center-34)*s, oy+85*s, ox+(center+34)*s, oy+85*s), fill="#6d7e95", width=2)
    _dashed(draw, (ox+179*s,oy+96*s), (ox+179*s,oy+307*s), "#73839a", 2, 7)
    _text(draw, (556, 874), "주황색·열 움직임 = 가열 중   |   회색 = 출력 없음", 18, MUTED, anchor="mt")

    chart_config = Config(target_temp_c=target, heater_max_avg_power_w=budget)
    for card, plot, title, ymin, ymax, color in _chart_specs(chart_config):
        draw.rounded_rectangle(card, radius=18, fill=CARD, outline=BORDER, width=2)
        _text(draw, (card[0]+25, card[1]+15), title, 22, color, True)
        x1, y1, x2, y2 = plot
        for tick in (ymin, (ymin+ymax)/2, ymax):
            _, y = _point(0, tick, plot, ymin, ymax, duration)
            draw.line((x1, y, x2, y), fill="#293b51", width=1)
            _text(draw, (x1-11, y), f"{tick:g}", 16, MUTED, anchor="rm")
        for tick in (0, 180, 360, 540, duration):
            if tick > duration:
                continue
            x, _ = _point(tick, ymin, plot, ymin, ymax, duration)
            _text(draw, (x, y2+10), _clock(tick), 15, MUTED, anchor="mt")
    temp_plot = _chart_specs(chart_config)[0][1]
    _, yy = _point(0, target, temp_plot, 20, 50, duration)
    _dashed(draw, (temp_plot[0], yy), (temp_plot[2], yy), "#ab885d", 2, 7)
    _text(draw, (1828, 343), f"목표 {target:g}°C", 18, ORANGE, anchor="rt")
    power_plot = _chart_specs(chart_config)[2][1]
    power_max = _chart_specs(chart_config)[2][4]
    _, yy = _point(0, budget, power_plot, 0, power_max, duration)
    _dashed(draw, (power_plot[0], yy), (power_plot[2], yy), "#7c7466", 2, 7)
    _text(draw, (1828, 739), f"예산 {budget:g}W", 18, MUTED, anchor="rt")

    draw.rounded_rectangle((56, 925, 1864, 1020), radius=18, fill="#112235", outline=BORDER, width=2)
    draw.rounded_rectangle((78, 992, 1842, 1000), radius=4, fill="#2c4158")
    _text(draw, (56, 1036), "가상 데이터 기반 데모 · 실물 센싱/구동 없음 · 전력 제한과 안전 차단은 신경망 출력 뒤에서 적용", 19, MUTED)
    return image


_ROWS_CACHE = {"id": None, "ends": None, "last": None, "fields": None}


def _prepare_rows(rows):
    # Cache only references and numerical point arrays; no duplicate frame data.
    if _ROWS_CACHE["id"] != id(rows) or _ROWS_CACHE["last"] != len(rows):
        _ROWS_CACHE.update({
            "id": id(rows), "last": len(rows),
            "ends": [float(r["interval_end_s"]) for r in rows],
            "fields": {key: [(float(r["time_s"]), float(r[key])) for r in rows]
                       for key in ("temp_BACK_c", "ambient_temp_c", "actual_power_w")},
        })
    return _ROWS_CACHE


def render_frame(rows: list[dict], config: Config, simulated_time_s: float,
                 width: int = 1920, height: int = 1080) -> Image.Image:
    """Render one RGB frame from completed intervals up to the given clock time."""
    if not rows:
        raise ValueError("simulation rows are required")
    duration = float(rows[-1]["interval_end_s"])
    now = min(duration, max(0.0, float(simulated_time_s)))
    cache = _prepare_rows(rows)
    index = max(0, bisect.bisect_right(cache["ends"], now) - 1)
    row = dict(rows[index])
    if now <= 0:
        row["heater_energy_wh"] = 0.0
        row["actual_power_w"] = 0.0
        row["applied_duty_pct"] = 0.0
        for p in PANELS:
            row[f"actual_{p}_w"] = 0.0
    image = _static_base(config.target_temp_c, config.heater_max_avg_power_w,
                         duration, config.ambient_profile).copy()
    draw = ImageDraw.Draw(image)
    vals = (f'{row["temp_BACK_c"]:.1f} °C', f'{ambient_temperature(config, now):+.1f} °C',
            f'{row["actual_power_w"]:.2f} W', f'{row["heater_energy_wh"]:.3f} Wh')
    for i, value in enumerate(vals):
        _text(draw, (79+i*457, 221), value, 46, (TEXT, BLUE, ORANGE, CYAN)[i], True)

    ox, oy, scale = 83, 378, 1.36
    positions = {
        PanelId.CHEST_L: (122,132,48,80,"왼쪽 가슴"),
        PanelId.CHEST_R: (186,132,48,80,"오른쪽 가슴"),
        PanelId.GLOVE_L: (31,258,45,62,"왼쪽 장갑"),
        PanelId.GLOVE_R: (283,258,45,62,"오른쪽 장갑"),
        PanelId.BACK: (460,132,88,112,"등판"),
    }
    for j, pid in enumerate(PANELS):
        px, py, pw, ph, label = positions[pid]
        x, y, w, h = ox+px*scale, oy+py*scale, pw*scale, ph*scale
        power = max(0.0, float(row[f"actual_{pid}_w"]))
        active = power > 0.001
        nominal = float(row.get("bus_voltage_v", 12.0)) ** 2 / config.panel_specs[pid].resistance_ohm
        intensity = min(1.0, power / nominal) if nominal else 0.0
        pulse = 0.90 + 0.10 * math.sin(now * math.tau / 10 + j * 0.6)
        color = _blend("#ffac4a", "#ff5d36", intensity) if active else _rgb("#334b67")
        if active:
            for spread, amount in ((14,0.10),(9,0.19),(4,0.34)):
                halo = _blend("#24364f", "#ff9342", amount * pulse)
                draw.rounded_rectangle((x-spread,y-spread,x+w+spread,y+h+spread), radius=13+spread, fill=halo)
        draw.rounded_rectangle((x,y,x+w,y+h), radius=12, fill=color,
                               outline="#ffd18b" if active else "#6b819d", width=2)
        for part in (0.26,0.46,0.66):
            draw.line((x+11,y+h*part,x+w-11,y+h*part), fill="#ffdfa9" if active else "#5d728d", width=3)
        if active:
            # Motion follows simulation time, slowed visually for clear 10x video.
            for k in range(3):
                phase = (now / 16 + k / 3 + j*0.17) % 1.0
                cx = x+w*(k+1)/4 + 4*math.sin(phase*math.tau)
                cy = y-6-44*phase
                rr = 2.5+3*phase
                heat = _blend(CARD, "#ffd294", (1-phase)*0.9)
                draw.ellipse((cx-rr,cy-rr,cx+rr,cy+rr), fill=heat)
        temp = float(row[f"temp_{pid}_c"])
        _text(draw, (x+w/2,y-28), f"{temp:.1f}°C", 22, TEXT, True, "mt")
        _text(draw, (x+w/2,y+h+6), f"{power:.1f} W", 22, TEXT, True, "mt")
        _text(draw, (x+w/2,y+h+35), label, 16, MUTED, False, "mt")

    keys = ("temp_BACK_c", "ambient_temp_c", "actual_power_w")
    for key, spec in zip(keys, _chart_specs(config)):
        _, plot, _, ymin, ymax, color = spec
        data = cache["fields"][key][:index+1]
        points = [_point(t,v,plot,ymin,ymax,duration) for t,v in data]
        if len(points) > 1:
            draw.line(points, fill=color, width=3, joint="curve")
        if points:
            x, y = points[-1]
            draw.ellipse((x-4,y-4,x+4,y+4), fill=color)
        cursor_x, _ = _point(now,ymin,plot,ymin,ymax,duration)
        draw.line((cursor_x,plot[1],cursor_x,plot[3]), fill="#64788f", width=1)

    state = row.get("system_state", "OFF")
    labels = {"OFF":"꺼짐", "SELF_TEST":"자가 점검", "READY":"시작 대기", "RUNNING":"가열 제어 중",
              "RECONNECT_HOLD":"재시작 대기", "FAULT_LATCHED":"오류 차단"}
    status = "시뮬레이션 완료" if now >= duration else labels.get(state,state)
    _text(draw, (79,941), status, 27, CYAN if not row.get("fault_codes") else "#ff807d", True)
    _text(draw, (420,943), f'공통 PWM  {row["applied_duty_pct"]:.1f}%', 25, TEXT, True)
    _text(draw, (905,945), f"영상 {_clock(now/10)} / 01:00", 22, MUTED)
    _text(draw, (1829,939), f"가상 {_clock(now)} / {_clock(duration)}", 31, TEXT, True, "rt")
    end_x = 78 + 1764 * now/duration
    if end_x > 78:
        draw.rounded_rectangle((78,992,end_x,1000), radius=4, fill=CYAN)
    if (width,height) != (1920,1080):
        image = image.resize((width,height), Image.Resampling.LANCZOS)
    return image
