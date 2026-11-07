"""Live playback of a Python thermal simulation; no physical hardware I/O."""
import bisect
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from thermallink.environment import ambient_temperature
from thermallink.heating_view import jacket_svg
from thermallink.models import Config, PANELS
from thermallink.neural import DEFAULT_MODEL, load_model
from thermallink.playback import Playback
from thermallink.reporting import csv_text
from thermallink.simulation import SCENARIOS, run_simulation

st.set_page_config(page_title="THERMAL LINK 가상 실험실", page_icon="🌡️", layout="wide")
st.caption("THERMAL LINK / HARDWARE Rev.C / VIRTUAL LAB")
st.title("발열자켓 가상 실험실")
st.write("실행을 누르면 가상 시간이 흐르며 온도·현재 전력·누적 에너지가 함께 변합니다.")
st.caption("가상 NTC와 가정값 기반 열 모델입니다. 실물 연결은 없습니다.")
config_path = Path(__file__).parent / "config" / "simulation.json"
try:
    base = Config.load(config_path) if config_path.exists() else Config()
except (ValueError, TypeError, KeyError, OSError) as exc:
    st.error(f"설정 파일을 확인하세요: {exc}")
    st.stop()

if st.session_state.get("view_version") != 3:
    for key in ("result", "playback", "comparison"):
        st.session_state.pop(key, None)
    st.session_state.view_version = 3

with st.sidebar:
    st.header("실험 설정")
    with st.form("simulation_settings"):
        scenario = st.selectbox("시험 시나리오", list(SCENARIOS), format_func=lambda key: SCENARIOS[key][0])
        duration = st.select_slider("가상 실행 시간 (초)", [180, 360, 600, 1200, 1800], value=600)
        target = st.slider("등판 목표 온도 (°C)", 25.0, 45.0, float(base.target_temp_c), 0.5)
        ambient_mode = st.radio("외기 온도 변화", ["−5 ↔ +5°C · 3분마다 선형 왕복", "고정 온도"])
        ambient = st.slider("고정 모드 외기 온도 (°C)", -20.0, 25.0, float(base.ambient_temp_c), 1.0)
        budget = st.slider("히터 평균 전력 예산 (W)", 0.0, 36.0, float(base.heater_max_avg_power_w), 0.5)
        mode = st.radio("제어 방식", ["신경망 제어 · PI 모방학습", "PI 자동 온도 제어", "수동 PWM 요청"])
        manual = st.slider("수동 요청 PWM (%)", 0.0, 100.0, 80.0, 1.0)
        with st.expander("전원 한도와 패드 저항"):
            current = st.number_input("공급원 연속 전류 한도 (A)", min_value=0.1, max_value=10.0, value=float(base.source_cont_current_a), step=0.1)
            peak = st.number_input("공급원 순간 전류 한도 (A)", min_value=0.1, max_value=15.0, value=float(base.source_peak_current_a), step=0.1)
            bus_peak = st.number_input("버스 순간 전류 한도 (A)", min_value=0.1, max_value=15.0, value=float(base.bus_peak_current_a), step=0.1)
            resistances = {p: st.number_input(f"{p} 저항 (Ω)", min_value=0.1, max_value=200.0,
                                             value=float(base.panel_specs[p].resistance_ohm), step=0.5) for p in PANELS}
        submitted = st.form_submit_button("시뮬레이션 실행", type="primary", width="stretch")
    speed = st.select_slider("재생 속도", [1, 5, 10, 30, 60], value=10, format_func=lambda x: f"{x}배", key="play_speed")
    st.caption("1배는 실제 시간과 같습니다. 10배에서는 가상 3분이 실제 18초에 지나갑니다.")

if submitted or "result" not in st.session_state:
    cfg = replace(base, target_temp_c=target, ambient_temp_c=ambient, heater_max_avg_power_w=budget,
                  ambient_profile="triangle" if ambient_mode.startswith("−5") else "constant",
                  ambient_min_c=-5.0, ambient_max_c=5.0, ambient_ramp_s=180.0,
                  source_cont_current_a=current, source_peak_current_a=peak, bus_peak_current_a=bus_peak,
                  panel_specs={p: replace(base.panel_specs[p], resistance_ohm=resistances[p]) for p in PANELS})
    if cfg.errors():
        st.error("설정을 수정하세요: " + "; ".join(cfg.errors()))
        st.stop()
    try:
        with st.spinner("열 모델과 제어 출력을 준비하고 있습니다…"):
            st.session_state.result = run_simulation(scenario, duration, cfg,
                manual if mode == "수동 PWM 요청" else None, "neural" if mode.startswith("신경망") else "pi")
    except (ValueError, OSError) as exc:
        st.error(f"모델을 확인하세요: {exc}")
        st.stop()
    st.session_state.playback = Playback(duration_s=float(duration), speed=float(speed))
    st.session_state.pop("comparison", None)
    if submitted:
        st.session_state.playback.start(time.monotonic())

result = st.session_state.result
clock = st.session_state.playback
clock.set_speed(float(speed), time.monotonic())
frame = pd.DataFrame(result.rows)
interval_ends = frame.interval_end_s.tolist()
state_labels = {"OFF": "꺼짐", "SELF_TEST": "자가 점검", "READY": "시작 대기", "RUNNING": "운전 중",
                "RECONNECT_HOLD": "재시작 대기", "FAULT_LATCHED": "오류 정지"}
control_labels = {"pi": "PI 자동 제어", "neural": "신경망 · PI 모방학습", "manual": "수동 PWM"}
st.subheader(SCENARIOS[result.scenario][0])
st.caption(f"{control_labels[result.controller_kind]} · {SCENARIOS[result.scenario][1]}")
if result.config.ambient_profile == "triangle":
    st.caption("외기: 0분 −5°C → 3분 +5°C → 6분 −5°C → 9분 +5°C … · 모든 패드의 열손실에 반영")

def pause():
    st.session_state.playback.pause(time.monotonic())

def resume():
    st.session_state.playback.resume(time.monotonic())

def restart():
    st.session_state.playback.start(time.monotonic())

def finish():
    st.session_state.playback.finish()

buttons = st.columns(2)
buttons[0].button("일시정지", on_click=pause, disabled=not clock.playing, width="stretch", key="pause")
buttons[1].button("재개", on_click=resume, disabled=clock.playing or clock.position_s >= clock.duration_s, width="stretch", key="resume")
buttons = st.columns(2)
buttons[0].button("처음부터 재생", on_click=restart, width="stretch", key="restart")
buttons[1].button("전체 결과 바로 보기", on_click=finish, width="stretch", key="finish")

@st.fragment(run_every=0.5 if clock.playing else None)
def live_view():
    playback = st.session_state.playback
    was_playing = playback.playing
    position = playback.tick(time.monotonic())
    if was_playing and not playback.playing:
        st.rerun()
    index = max(0, bisect.bisect_right(interval_ends, position) - 1)
    visible = frame.iloc[:index + 1]
    row = visible.iloc[-1].to_dict()
    status = "재생 중" if playback.playing else "완료" if position >= playback.duration_s else "대기 / 일시정지"
    st.progress(position / playback.duration_s,
                text=f"{status} · 가상 {position:.1f} / {playback.duration_s:g}초 · {playback.speed:g}배")
    cols = st.columns(2)
    cols[0].metric("현재 등판 온도", f'{row["temp_BACK_c"]:.1f} °C')
    cols[1].metric("현재 외기 온도", f'{ambient_temperature(result.config, position):+.1f} °C')
    cols = st.columns(2)
    cols[0].metric("현재 히터 전력", f'{row["actual_power_w"]:.2f} W')
    cols[1].metric("누적 히터 에너지", f'{row["heater_energy_wh"]:.3f} Wh')
    st.caption(f'현재 {state_labels[row["system_state"]]} · 최종 PWM {row["applied_duty_pct"]:.1f}% · '
               f'지금까지 최대 전력 {visible.actual_power_w.max():.2f}W')
    if row["fault_codes"]:
        st.error("유지 중인 오류: " + row["fault_codes"])
    components.html('<style>html,body{margin:0;background:#08111e}svg{display:block;width:100%;height:280px}</style>'
                    + jacket_svg(row, position), height=300, scrolling=False)
    st.caption("발열 효과는 패드별 실제 전력에 연동됩니다. 일시정지하면 효과도 멈춥니다.")
    overview, environment, event_tab = st.tabs(["온도 · PWM · 전력", "외기 온도", "패드와 이벤트"])
    with overview:
        temps = visible[["time_s"] + [f"temp_{p}_c" for p in PANELS]].rename(columns={f"temp_{p}_c": p.value for p in PANELS})
        temps["등판 목표"] = result.config.target_temp_c
        st.line_chart(temps, x="time_s", x_label="가상 시간 (초)", y_label="온도 (°C)", height=280)
        st.caption("센서 오류·분리 구간은 참고용 가상 물리 온도입니다. 센서 유효 여부를 함께 확인하세요.")
        pwm = visible[["time_s", "controller_duty_pct", "power_limited_duty_pct", "applied_duty_pct"]].rename(
            columns={"controller_duty_pct": "제어 요청", "power_limited_duty_pct": "전력 제한 후", "applied_duty_pct": "최종 적용"})
        st.line_chart(pwm, x="time_s", x_label="가상 시간 (초)", y_label="PWM (%)", height=230)
        power = visible[["time_s", "budget_w", "actual_power_w"]].rename(columns={"budget_w": "전력 예산", "actual_power_w": "실제 히터 전력"})
        st.line_chart(power, x="time_s", x_label="가상 시간 (초)", y_label="전력 (W)", height=230)
    with environment:
        st.line_chart(visible[["time_s", "ambient_temp_c"]].rename(columns={"ambient_temp_c": "외기 온도"}),
                      x="time_s", x_label="가상 시간 (초)", y_label="외기 (°C)", height=280)
        st.write("외기는 연속적으로 변합니다. 등판은 열용량 때문에 늦게 반응하며, 제어기는 온도 오차에 따라 출력을 조절합니다.")
    with event_tab:
        table = [{"패드": p.value, "온도 °C": round(row[f"temp_{p}_c"], 2), "NTC 유효": row[f"sensor_{p}_valid"],
                  "연결": row[f"presence_{p}"], "스위치 닫힘": row[f"switch_{p}_closed"],
                  "실제 전력 W": round(row[f"actual_{p}_w"], 3)} for p in PANELS]
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame([e for e in result.events if e["time_s"] <= position]), hide_index=True, width="stretch")

live_view()

with st.expander("신경망 구조 · 학습 · PI 비교"):
    st.markdown("**온도 오차 + 적분 상태 → 신경망 2 → 16 → 8 → 1 → PWM 요청 → 전력 제한 → 안전 차단**")
    st.write("가상 온도 오차와 적분 상태 8,000개에서 PI의 출력을 모방 학습했습니다. 별도 2,000개로 출력 오차를 계산했습니다.")
    st.caption("독립적인 최적 제어를 학습한 모델은 아닙니다. 적분 상태 갱신은 PI 규칙입니다. 외기는 열 모델과 등판 센싱값을 통해 간접 반영됩니다.")
    model = load_model()
    metadata = model["metadata"]
    st.write(f'저장된 모델: {metadata["epochs"]}회 학습 · PI 대비 평균 PWM 차이 **{metadata["validation_mae_pct_points"]:.3f}%p**')
    st.line_chart(pd.DataFrame(metadata["history"]), x="epoch", x_label="학습 반복", y_label="검증 PWM 오차 (%p)", height=220)
    if st.button("가상 데이터로 신경망 다시 학습", key="train_model"):
        from thermallink.training import train_model
        with st.spinner("가상 데이터로 학습 중…"):
            trained = train_model(kp=base.kp, ki=base.ki)
        st.success(f'학습 완료 · 평균 PWM 차이 {trained["validation_mae_pct_points"]:.3f}%p. 다음 실행부터 적용됩니다.')
    if st.button("같은 조건에서 PI와 신경망 비교", key="compare"):
        with st.spinner("같은 외기 조건으로 계산 중…"):
            pi = run_simulation(result.scenario, clock.duration_s, result.config, controller_kind="pi")
            neural = run_simulation(result.scenario, clock.duration_s, result.config, controller_kind="neural")
            st.session_state.comparison = (pi, neural)
    if "comparison" in st.session_state:
        pi, neural = st.session_state.comparison
        comparison = pd.DataFrame({"time_s": [r["time_s"] for r in pi.rows],
                                   "PI 등판": [r["temp_BACK_c"] for r in pi.rows],
                                   "신경망 등판": [r["temp_BACK_c"] for r in neural.rows]})
        st.line_chart(comparison, x="time_s", x_label="전체 가상 시간 (초)", y_label="등판 온도 (°C)", height=250)
        st.dataframe(pd.DataFrame([{"제어": label, "최종 등판 °C": round(item.rows[-1]["temp_BACK_c"], 2),
                                    "누적 에너지 Wh": round(item.rows[-1]["heater_energy_wh"], 3)}
                                   for label, item in (("PI", pi), ("신경망", neural))]), hide_index=True)

with st.expander("전체 기록 다운로드 · 사용한 가정값"):
    st.caption("다운로드는 전체 시간의 계산 결과이며, 화면은 이 결과를 시간 순서대로 재생합니다.")
    st.download_button("전체 CSV 다운로드", csv_text(result.rows).encode("utf-8-sig"),
                       f"thermallink_{result.scenario}_{result.controller_kind}.csv", "text/csv")
    export = {"config": asdict(result.config), "controller_kind": result.controller_kind,
              "model_metadata": result.model_metadata}
    st.download_button("실험 설정 다운로드", json.dumps(export, ensure_ascii=False, indent=2), "experiment.json", "application/json")
    st.download_button("학습된 신경망 가중치 다운로드", DEFAULT_MODEL.read_bytes(), "pi_imitation.json", "application/json")
    st.json(export, expanded=False)
