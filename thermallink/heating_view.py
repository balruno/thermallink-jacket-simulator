"""Jacket SVG driven by actual panel power after every safety gate."""
import math
from .models import PANELS, PanelId


def jacket_svg(row: dict, time_s: float) -> str:
    positions = {
        PanelId.CHEST_L: (122, 132, 48, 80, "왼쪽 가슴"),
        PanelId.CHEST_R: (186, 132, 48, 80, "오른쪽 가슴"),
        PanelId.GLOVE_L: (31, 258, 45, 62, "왼쪽 장갑"),
        PanelId.GLOVE_R: (283, 258, 45, 62, "오른쪽 장갑"),
        PanelId.BACK: (460, 132, 88, 112, "등판"),
    }
    out = ['''<div style="width:100%;max-width:900px;margin:auto">
    <svg viewBox="0 0 660 390" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="패드별 발열 상태">
    <defs><filter id="thermal-glow"><feGaussianBlur stdDeviation="6"/></filter></defs>
    <style>.tl-label{font-family:Arial,'Malgun Gothic',sans-serif;fill:#c4d3e6;font-size:12px}
    .tl-value{font-family:Arial,sans-serif;fill:#fff;font-size:12px;font-weight:600}
    .tl-heading{font-family:Arial,'Malgun Gothic',sans-serif;fill:#b8cce5;font-size:15px}</style>
    <rect width="660" height="390" rx="20" fill="#101e31"/>
    <text x="179" y="31" text-anchor="middle" class="tl-heading">앞면 · 가슴 / 장갑</text>
    <text x="505" y="31" text-anchor="middle" class="tl-heading">뒷면 · 등판</text>
    <path d="M137 69 L111 79 L62 119 L29 247 L78 260 L108 160 L108 308 L250 308 L250 160 L280 260 L330 247 L296 119 L247 79 L220 69 Q180 100 137 69Z" fill="#23344d" stroke="#50617a" stroke-width="2"/>
    <path d="M137 69 Q144 40 179 40 Q213 40 220 69 L205 91 L153 91Z" fill="#1b2941" stroke="#50617a"/>
    <path d="M179 94 L179 307" stroke="#697892" stroke-width="2" stroke-dasharray="4 4"/>
    <path d="M463 69 L438 79 L387 119 L371 232 L415 241 L434 158 L434 308 L575 308 L575 158 L596 241 L638 232 L622 119 L572 79 L548 69 Q505 100 463 69Z" fill="#23344d" stroke="#50617a" stroke-width="2"/>
    <path d="M463 69 Q469 40 505 40 Q540 40 548 69 L531 92 L479 92Z" fill="#1b2941" stroke="#50617a"/>''']
    for j, pid in enumerate(PANELS):
        x, y, w, h, label = positions[pid]
        power = max(0.0, float(row[f"actual_{pid}_w"]))
        temp = float(row[f"temp_{pid}_c"])
        attached = row[f"presence_{pid}"] == "ATTACHED"
        active = power > 0.001
        nominal = 12 if pid == PanelId.BACK else 6 if "CHEST" in pid else 3
        intensity = min(1.0, power / nominal)
        color = f"rgb(255,{int(176 - 90 * intensity)},55)" if active else "#31465f"
        opacity = 0.35 + 0.55 * intensity
        if active:
            out.append(f'<rect x="{x-4}" y="{y-4}" width="{w+8}" height="{h+8}" rx="12" fill="{color}" opacity="{opacity:.3f}" filter="url(#thermal-glow)"/>')
        dash = 'stroke-dasharray="5 4"' if not attached else ""
        border = "#ffc47d" if active else "#74869d"
        out.append(f'<rect data-panel="{pid}" data-heating="{str(active).lower()}" x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{color}" stroke="{border}" {dash}/>')
        for offset in (0.28, 0.48, 0.68):
            stroke = "#ffe0ae" if active else "#566b85"
            out.append(f'<path d="M{x+9} {y+h*offset:.1f} h{w-18}" stroke="{stroke}" stroke-width="2" fill="none"/>')
        if active:
            for k in range(3):
                phase = (time_s / 2.4 + k / 3 + j * 0.17) % 1
                cx = x + w * (k + 1) / 4 + 3 * math.sin(phase * math.tau)
                cy = y - 3 - 32 * phase
                out.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{2+phase*2:.2f}" fill="#ffbc75" opacity="{(1-phase)*opacity:.3f}"/>')
        out.append(f'<text x="{x+w/2}" y="{y+h+16}" text-anchor="middle" class="tl-value">{power:.1f} W</text>')
        out.append(f'<text x="{x+w/2}" y="{y+h+32}" text-anchor="middle" class="tl-label">{label}</text>')
        out.append(f'<text x="{x+w/2}" y="{y-10}" text-anchor="middle" class="tl-value">{temp:.1f}°C</text>')
    out.append('<text x="330" y="378" text-anchor="middle" class="tl-label">주황색·열 표시 = 가열 중 · 회색 = 출력 없음 · 점선 = 분리됨</text></svg></div>')
    return "".join(out)
