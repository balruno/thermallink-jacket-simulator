"""Dependency-free CSV/JSON/HTML/SVG reports."""
import csv
import html
import io
import json
from pathlib import Path

from .models import PANELS
from .simulation import SCENARIOS, SimulationResult


COLORS = ["#44d9d2", "#ffb454", "#ac9dff", "#69c987", "#ef8cab"]


def csv_text(rows: list[dict]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def chart_svg(rows: list[dict], fields: list[tuple[str, str]], title: str, unit: str,
              references: list[tuple[float, str]] | None = None) -> str:
    references = references or []
    width, height = 1060, 310
    left, right, top, bottom = 62, 22, 70, 42
    pw, ph = width - left - right, height - top - bottom
    xmax = max(rows[-1]["interval_end_s"], 1)
    values = [r[f] for r in rows for f, _ in fields] + [v for v, _ in references]
    ymin, ymax = min(0, min(values)), max(1, max(values)) * 1.08
    def xy(t, v):
        return left + t / xmax * pw, top + (ymax - v) / (ymax - ymin) * ph
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(title)}">',
           '<style>text{font-family:Segoe UI,Malgun Gothic,sans-serif;fill:#b8c7db;font-size:12px}</style>',
           f'<text x="{left}" y="24" style="font-size:17px;fill:#f0f5ff;font-weight:600">{html.escape(title)} · {unit}</text>']
    for i in range(5):
        v = ymin + (ymax - ymin) * i / 4
        _, y = xy(0, v)
        out += [f'<line x1="{left}" y1="{y}" x2="{width-right}" y2="{y}" stroke="#233650"/>',
                f'<text x="{left-10}" y="{y+4}" text-anchor="end">{v:.0f}</text>']
    for i in range(7):
        t = xmax * i / 6
        x, _ = xy(t, 0)
        out.append(f'<text x="{x}" y="{height-15}" text-anchor="middle">{t:.0f}s</text>')
    for value, label in references:
        _, y = xy(0, value)
        out += [f'<line x1="{left}" y1="{y}" x2="{width-right}" y2="{y}" stroke="#73869f" stroke-dasharray="5 5"/>',
                f'<text x="{width-right-5}" y="{y-5}" text-anchor="end">{html.escape(label)}</text>']
    for j, (field, label) in enumerate(fields):
        color = COLORS[j % len(COLORS)]
        points = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(r["time_s"], r[field]) for r in rows))
        out.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        lx = left + j * 185
        out += [f'<line x1="{lx}" y1="48" x2="{lx+18}" y2="48" stroke="{color}" stroke-width="3"/>',
                f'<text x="{lx+24}" y="52">{html.escape(label)}</text>']
    out.append('</svg>')
    return "".join(out)


def save_results(results: list[SimulationResult], directory: str | Path) -> Path:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    summaries = [r.summary() for r in results]
    (root / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    sections = []
    for result in results:
        name = result.scenario
        (root / f"{name}.csv").write_text(csv_text(result.rows), encoding="utf-8-sig")
        (root / f"{name}_events.json").write_text(json.dumps(result.events, ensure_ascii=False, indent=2), encoding="utf-8")
        result.config.save(root / f"{name}_config.json")
        charts = [
            chart_svg(result.rows, [("ambient_temp_c", "Ambient")], "외기 온도", "°C"),
            chart_svg(result.rows, [(f"temp_{p}_c", p.value) for p in PANELS], "패드별 온도", "°C",
                      [(result.config.target_temp_c, "BACK target"), (result.config.temp_cutoff_c, "NTC cutoff")]),
            chart_svg(result.rows, [("controller_duty_pct", "Controller"), ("power_limited_duty_pct", "Power limited"),
                                    ("applied_duty_pct", "Final PWM")], "공통 PWM의 3단계", "%"),
            chart_svg(result.rows, [("budget_w", "Budget"), ("estimated_power_w", "Conservative estimate"),
                                    ("actual_power_w", "Virtual actual")], "히터 전력", "W"),
        ]
        for suffix, chart in zip(("ambient", "temperature", "pwm", "power"), charts):
            (root / f"{name}_{suffix}.svg").write_text(chart, encoding="utf-8")
        events = "".join(f'<tr><td>{e["time_s"]:g}s</td><td>{html.escape(e["message"])}</td></tr>' for e in result.events)
        summary = result.summary()
        sections.append(f'''<section id="{name}"><h2>{html.escape(SCENARIOS[name][0])}</h2>
        <p>{html.escape(SCENARIOS[name][1])} · 제어: {result.controller_kind} · 외기: {result.config.ambient_profile}</p><div class="metrics">
        <div><b>{summary["max_actual_power_w"]:.2f} W</b><span>최대 실제 히터 전력</span></div>
        <div><b>{summary["heater_energy_wh"]:.3f} Wh</b><span>누적 히터 에너지</span></div>
        <div><b>{summary["final_state"]}</b><span>최종 시스템 상태</span></div></div>
        {''.join(charts)}<details><summary>상태 변화와 오류 이벤트 · {len(result.events)}건</summary>
        <table><thead><tr><th>시간</th><th>이벤트</th></tr></thead><tbody>{events}</tbody></table></details>
        <p><a href="{name}.csv">CSV 기록</a> · <a href="{name}_config.json">사용한 가정값</a></p></section>''')
    links = "".join(f'<a href="#{r.scenario}">{html.escape(SCENARIOS[r.scenario][0])}</a>' for r in results)
    document = '''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>THERMAL LINK · Rev.C 시뮬레이션</title><style>
    :root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#08111e;color:#e8eff8;font:15px/1.7 'Segoe UI','Malgun Gothic',sans-serif}
    main{max-width:1140px;margin:auto;padding:52px 28px}h1{font-size:38px;margin:8px 0}h2{font-size:25px;margin:0 0 4px}
    p{color:#aebfd3}a{color:#67e3dc;text-decoration:none}a:hover{text-decoration:underline}.eyebrow{color:#67e3dc;letter-spacing:3px;font-size:12px}
    .notice{border-left:3px solid #ffb454;background:#172132;padding:14px 20px;margin:24px 0;color:#d6e0ee}
    nav{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0}nav a{padding:7px 12px;border:1px solid #2a3d53;border-radius:20px;font-size:13px}
    section{border:1px solid #24354b;border-radius:16px;padding:28px;background:#0f1b2d;margin:28px 0;scroll-margin-top:20px}
    .metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:22px 0}.metrics div{background:#142238;border-radius:10px;padding:18px}
    b{display:block;font-size:24px}span{color:#9fb2ca;font-size:13px}svg{width:100%;height:auto;display:block;margin:18px 0}
    details{margin-top:20px}summary{cursor:pointer;color:#67e3dc}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:12px}
    td,th{text-align:left;border-bottom:1px solid #26374e;padding:9px}td:first-child{width:80px;color:#ffb454}
    footer{color:#95aac3;font-size:13px}@media(max-width:650px){main{padding:24px 12px}section{padding:14px}.metrics{grid-template-columns:1fr}h1{font-size:28px}}
    </style></head><body><main><div class="eyebrow">THERMAL LINK / REV.C / VIRTUAL LAB</div><h1>자율제어 발열자켓 시뮬레이션</h1>
    <p>5개 발열패드 · NTC 5개 · 공통 PWM 1채널 · PI 또는 신경망 모방 제어</p>
    <div class="notice">이 결과는 가정값을 사용한 소프트웨어 시뮬레이션입니다. 실물 정격·착용 온도·ESP32 구동 검증을 의미하지 않습니다.
    물리 스위치 상태는 가상 모델에서만 관측하며 제어기에는 전달하지 않습니다.</div>'''
    document += f'<nav>{links}</nav>' + "".join(sections)
    document += '''<footer>온도는 구간 시작 샘플, 실제 전력은 해당 구간 평균, 스위치 상태는 구간 끝 기준입니다.
    CSV의 plant_* 값은 가상 물리 온도이며, temp_* 값은 유효 수치 샘플 또는 센서 부재 시 참고용 물리 온도입니다.
    센서 유효 여부는 sensor_*_valid를 함께 확인하세요. 효율은 공급원 히터 분기 → 발열 버스에 한 번 적용합니다.</footer></main></body></html>'''
    path = root / "report.html"
    path.write_text(document, encoding="utf-8")
    return path
