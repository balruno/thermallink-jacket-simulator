# 04 구현 계약 — TL-REVC-5P-1PWM-v1

## 화면·외기·신경망 추가

신경망은 가상 PI 모방 데이터로 학습한 2→16→8→1 MLP이며, PWM 요청 단계에만 참여합니다. 기존 전력 제한과 안전 게이트의 책임은 유지됩니다. 적분 상태는 기존 PI 규칙으로 갱신합니다. 외기 온도는 직접 신경망 입력이 아니라 열 모델을 통해 등판 온도에 반영됩니다.

외기 `triangle`은 기본 −5~+5°C, 한 방향 180초의 연속 선형 왕복입니다. `constant`는 기존 고정 외기입니다. CSV에 `ambient_temp_c`를 추가했고, 열 모델의 각 하위 구간 중간 시각 외기를 사용합니다. 화면은 계산된 결과를 가상 시계에 맞춰 재생합니다. 현재 전력과 누적 에너지를 분리 표시하며, 패드 효과는 실제 전력에 연동합니다.

기준은 제공된 최종 대조 체크리스트입니다. 02 POWER 전체 소스와 03 HARDWARE Rev.C 원문 전체를 추가로 확보한 것으로 간주하지 않습니다. 아래는 시뮬레이션 구현을 위해 선택한 계약이며 실물 승인 문서가 아닙니다.

## 충돌 해결

| 항목 | 채택한 결정 |
|---|---|
| C01/C02 | 5개 표준 패드만 허용. 이전 `CHEST`, `CUFF_L/R`는 받지 않음 |
| C03 | BACK만 제어 입력, 모든 장착 NTC는 안전 감시 |
| C04 | 정상 분리 부팅 장갑은 부하·센서 대상에서 제외. 운전 중 연결 변화는 전체 HOLD |
| C05 | 입출력 PWM은 %, `allocate_power` 내부는 비율. 명시적으로 /100, ×100 |
| C06 | 오류 래치는 `SafetyController` 한 곳만 소유. `PowerState`는 연결 이력만 보관 |
| C07 | 연속 한도는 평균 예산에 반영. 공급원·버스 순간 한도 초과는 무조건 차단 |

`allocate_power(config, step_input, previous_state) -> (power_output, next_state)` 함수 형태는 유지합니다. 입력 구조는 5패드에 맞게 변경했으며 예전 4패드 소스와 드롭인 호환되는 구현이라는 주장은 하지 않습니다.

`PowerStatus`는 `NORMAL`, `POWER_LIMITED`, `IDLE`, `NO_POWER`, `CONFIG_ERROR`, `PEAK_LIMIT`을 사용합니다. 이전 POWER의 `SENSOR_FAULT`, `OVER_TEMP`, `FAULT_LATCHED` 의미는 안전 계층의 오류·상태로 이동했습니다. `PowerOutput.fault_code`는 해당 시점의 오류이며 지속 래치가 아닙니다.

## 전력 모델

```text
P_source = min(source_cont_power_w, V_source × source_cont_current_a)
B = min(heater_max_avg_power_w, η × max(0, P_source − P_idle))
P_on_i = V_bus² / R_i             # 장착된 모든 패드, 모든 스위치 닫힘 가정
P_on = Σ P_on_i
I_bus_on = P_on / V_bus
I_source_on = (P_on / η + P_idle) / V_source
D_power = min(D_request, 1, B / P_on)
D_final = D_power if RUNNING and no faults else 0
P_estimated_i = D_final × P_on_i
P_virtual_i = D_final × P_on_i × actual_switch_closed_i
I_source_average = (Σ P_virtual_i / η + P_idle) / V_source
```

부하 또는 예산이 0이면 듀티는 0입니다. 순간 한도 초과는 요청이 0%여도 래치합니다. 전류 검사는 관측 불가능한 물리 스위치를 이유로 완화하지 않습니다. 연속 전류 한도는 평균 입력 전류에, 순간 전류 한도는 ON 구간에 적용합니다. 공급원의 실제 펄스 허용특성은 미확정입니다.

`PowerOutput.duty_applied`와 `power_actual_w`는 전력 제한기 단계의 후보입니다. 최종 출력은 `StepOutput.applied_duty_pct`, 게이트 후 계산은 `estimated_power_w`입니다. 가상 스위치까지 반영한 최종 결과는 CSV의 `actual_power_w`입니다. 차단 시 히터 출력은 모두 0이며, 유효한 전원에서는 비발열 입력 소비전력은 남습니다.

## 상태와 이벤트

```text
OFF --BOOT--> SELF_TEST --정상 + 연결 안정--> READY --START--> RUNNING
RUNNING --STOP--> READY
RUNNING --연결 변화--> RECONNECT_HOLD --연결 안정 + 별도 START--> RUNNING
모든 상태 --오류--> FAULT_LATCHED
FAULT_LATCHED --원인 해소 + 전 장착 NTC ≤ 복귀 온도 + 연결 안정 + RESET--> READY
모든 상태 --OFF--> OFF (기존 오류 래치는 보존)
```

기본 연결 안정 시간은 2초입니다. RESET과 START는 서로 다른 명령이며 같은 제어 스텝에 동시에 처리할 수 없습니다. HOLD의 STOP으로 안정 대기를 우회할 수 없습니다. OFF에서도 오류를 기록하며, OFF 명령이 들어온 해당 스텝의 표시 상태만 OFF가 우선합니다. 이후 남아 있는 오류는 FAULT_LATCHED로 표시됩니다.

접점 `UNSTABLE`/`UNKNOWN`은 래치 오류이며 정상 ATTACHED↔DETACHED 변경에 따른 HOLD와 다릅니다. 본체 패드 3개는 필수입니다. 장갑을 제외한 패드의 DETACHED는 오류입니다.

SELF_TEST는 입력·설정·연결 검증을 의미합니다. 실제 배선·FET 단락·ADC 자가 진단이 아닙니다. 오류 래치는 파이썬 객체 메모리에 유지되며 비휘발성 저장은 구현하지 않았습니다. 새 객체/프로세스는 OFF부터 시작해 별도 BOOT와 START가 필요합니다.

## 오류 검사

- 설정 숫자의 NaN/무한대/부호·범위 위반과 파생 전력 계산 오버플로는 안전 차단.
- 장착 NTC의 OPEN, SHORT, RANGE, NO_SAMPLE, 무효값, 미래/지연 샘플은 래치.
- 수신 시각이 반복·역행하거나 제어 간격이 설정 한도를 넘으면 래치.
- 소프트웨어 과열 기준과 물리 스위치 개폐를 별도로 처리.
- 고장 해소만으로는 재가동하지 않음. RESET 후에도 READY에서 PWM 0.

## 열 모델

```text
C_i dT_i/dt = P_i − G_ambient_i(T_i − T_ambient) − G_body_i(T_i − T_body)
```

0.05초 이하 가상 하위 구간에서 구간별 상수 전력에 대한 해석해를 적용합니다. 물리 온도 스위치는 55°C 개방, 가정된 48°C 복귀의 히스테리시스를 가집니다. 스위치 위치 온도는 패드 온도와 별도 5초 지연 모델을 사용합니다. 가상 스위치 상태는 제어 입력 계약에 포함되지 않습니다.

## 수용 기준과 검증 범위

| 항목 | 자동 검증 기대 결과 |
|---|---|
| H01~H03 | BOOT→SELF_TEST→READY, START 후 가열. 전력 예산 준수 |
| H04 | 모든 NTC 중 하나라도 차단 온도 도달 시 공통 PWM 0·래치 |
| H05 | 5개 NTC 각 OPEN/SHORT/RANGE/NO_SAMPLE 시험, 전체 차단 |
| H06 | 정상 분리 장갑 제외. 해당 센서 부재를 단선으로 오판하지 않음 |
| H07~H09 | 운전 중 분리·재연결은 전체 HOLD. 안정 후 새 START 필요 |
| H10 | 가상 물리 스위치 개방은 해당 패드 전력만 0 |
| H11 | 냉각 시 스위치 자동복귀. PWM 0일 때도 물리 상태는 변함 |
| H12 | 원인 해소·온도 복귀·RESET 필수. RESET 후 READY, 별도 START |
| H13 | 공급원·버스 전압 범위 오류로 전체 차단 |
| H14 평균 | POWER_LIMITED에서 제한 가열 유지 |
| H14 순간 | 매우 작은 요청 듀티여도 PEAK_LIMIT·래치 |
| H15 부분 | 가상 어댑터 입력 JSON 왕복 및 최종 출력 계약 일치 |
| H15 제외 | ESP32 핀·ADC·PWM·워치독·통신·실물 포팅 검증은 미실행 |

추가로 무작위 전력 조건 300건의 예산/듀티 불변식, 11개 시나리오의 게이트 후 출력, 열 방정식 해석해, 정상 온도 수렴, 잘못된 설정·샘플·타이밍, 화면 설정과 실행 버튼을 검사합니다. H 번호에 대한 상세 테스트 정의는 첨부 체크리스트 수준이며 별도 Rev.C 전체 시험 원문과의 완전한 대조를 뜻하지 않습니다.
