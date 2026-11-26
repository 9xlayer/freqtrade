# MTF Sniper — Kiến Trúc Hệ Thống

**Package:** `strategies/mtf_sniper/` · **Version:** `STRATEGY_VERSION` trong `__init__.py` (`4.7.0-1w-regime`) · **Deploy:** Docker → `--strategy-path …/mtf_sniper`

**Bất biến cốt lõi:** Kiến trúc phân lớp chặt chẽ. Toàn bộ luật từ bản chiến lược cũ được đưa vào các tầng chuyên biệt; đường vào lệnh live luôn đi qua `EntryPipeline` (không bao giờ đánh tắt qua Policy ngay ở lớp chiến lược).

**Hồ sơ vận hành (Runtime profile):**
- **Local / Backtest (`deterministic`):** Dùng bộ chấm điểm tất định `JEV_MODE=native`, `enable_waterfall=true` (JevHunter đã gỡ hoàn toàn).
- **Docker Production (`decision_external`):** Dùng `JEV_MODE=external` Hybrid AI score (`w_ai=0.10`, `JEV_FAIL_POLICY=fallback_native`, timeout `4s`), `enable_waterfall=true`, `enable_dynamic_sizing=true`, `exchange_sync_interval_secs=15`. Xem thêm `STABILIZATION_DETERMINISTIC.md`.

**Tài liệu gốc chuẩn:** File này + `PHILOSOPHY.md` (triết lý chiến lược & lý do thiết kế) + `DATA_CONTRACTS.md` (hợp đồng dữ liệu) mô tả đúng hành vi đang chạy thực tế. Ghi chú lịch sử chuyển đổi nằm ở `MIGRATION_PLAN.md`.

---

## 0. Lớp Giao Tiếp (`StrategyMTFSniper`) vs Các Module Bên Trong

Freqtrade gọi trực tiếp vào `StrategyMTFSniper`; lớp này đóng vai trò facade mỏng điều phối sang các module:

| Hook Freqtrade | Vai trò chiến lược | Module xử lý |
|---|---|---|
| `bot_loop_start` | Tự làm mới vị thế live mỗi `15s` khi đang có lệnh mở (phát hiện đóng tay trên sàn) | `strategy_mtf_sniper.py` |
| `populate_indicators_*` | Tính chỉ báo đa khung (`1D`, `4H`, `1H`, `15m`) | `market/indicators.py` |
| `populate_entry_trend` | Sinh tín hiệu vào lệnh + cooldown + ghi danh `CandidateRegistry` | `execution/entry_pipeline.py` |
| `leverage` / `custom_stake_amount` | Đòn bẩy (tối đa `10x`) + stake theo tag & ngược pha 1D (`0.50x`) | `risk/position_sizing.py` |
| `confirm_trade_entry` | Kiểm tra bảo vệ danh mục + JEV Decision + ghi `DecisionLedger` | `intelligence/` + `risk/` |
| `custom_stoploss` | Thang khoá lãi theo bậc ROE + cắt lỗ kẹt 24h | `risk/stoploss_ladder.py` |
| `confirm_trade_exit` | Hoãn chốt ROI `>= 50%` khi xu hướng 1H vẫn giữ | `execution/exit_policy.py` |
| `custom_exit` | Tắt (`use_exit_signal = False`) | `strategy_mtf_sniper.py` |
| `order_filled` | Lưu `mtf_candidate_id` vào Trade custom data + ghi `TradeLedger` | `core/candidate_*`, `telemetry/` |

**Nguyên tắc tín hiệu:**

1. **Sensors** chỉ phát hiện từng **sự kiện thành phần (part facts)** — tuyệt đối không ghi `enter_*`, không tính size, không gọi JEV.
2. Đủ bộ 4 phần chuẩn (ALL-4 parts) mới tạo thành một `TradeCandidate` gắn nhãn `setup:sniper_*`.
3. Khi `enable_waterfall=True`, `WaterfallMomentumSensor` (`waterfall_momentum`) phát hiện đà rơi theo `MomentumRadar` để `SetupComposer` gắn sub-system `waterfall` và chuyển thẳng qua `WaterfallShortPolicy` (lọc 2 chế độ Vacuum Breakdown / In-Range Collapse).
4. **Policy** quyết định **quyền hợp lệ (`is_valid`)** và gắn thêm **điểm chất lượng (`quality_grade`)**.
5. Thành phần rời rạc chưa đủ bộ $\neq$ mở lệnh.

---

## 1. Kiến Trúc Phân Lớp Toàn Luồng (End-to-End Flow)

```mermaid
flowchart TB
  subgraph L1 ["1. Market Layer - market/indicators.py"]
    ind1d["1D Informative: EMA50/200, swing_low_20d, PDH, macro bull/bear"]
    ind4h["4H Informative: MACD wave, EMA50, ADX, DI, BB width"]
    ind1h["1H Informative: EMA21/50, MACD, RSI 24h min/max"]
    ind15m["15m Base: EMA10/21, ATR pct, rel_vol, wick/close ratios, long/short_score"]
  end

  subgraph L2 ["2. Signal & Setup Composition (Single Truth) - signals/ & directional/setup_composer.py"]
    suite["SensorSuite.live: 8 LegacySniperPartSensor + WaterfallMomentumSensor"]
    scanMask["BaseSensor.scan_mask() (vector hoa, khong tao object rac) hoac Event fallback"]
    composer["SetupComposer.compose_suite() ghep SetupRecipe trong SUBSYSTEM_REGISTRY"]
    candOut["Chuan hoa TradeCandidate (subsystem tag + canonical candidate_id)"]
    suite --> scanMask --> composer --> candOut
  end

  subgraph L3 ["3. Directional Policy & Registry - directional/"]
    shortPol["ShortPolicy (short_sniper)"]
    wfPol["WaterfallShortPolicy (waterfall Dual-Regime)"]
    longPol["LongPolicy (long_sniper)"]
    hunterOff["HunterEscalationGateway (tat tren live)"]
  end

  subgraph L4 ["4. Post-Policy & Identity - core/"]
    cooldown["apply_cooldown (40 long / 96 short) + huy xung dot Long/Short"]
    enterCols["Ghi enter_long / enter_short / enter_tag theo vi tri iloc"]
    regMem["CandidateRegistry.remember (chi luu tin hieu song sot sau cooldown)"]
    cooldown --> enterCols --> regMem
  end

  subgraph L5 ["5. Sizing & Confirm Gate - risk/ & intelligence/"]
    sizing["leverage() & custom_stake_amount() -> position_sizing.py (0.50x counter-1D)"]
    confirm["confirm_trade_entry() -> _resolve_candidate_id"]
    portGuard["portfolio_protection: MaxOpen, CB (reset + cross-pair dedup), DirLock, Distress"]
    jevGate["JevDecisionAdapter (native HOẶC external Hybrid + fallback_native)"]
    riskAuth["RiskEngine.authorize_entry -> RiskAuthorization + DecisionLedger (15m dedup)"]
    confirm --> portGuard --> jevGate --> riskAuth
  end

  subgraph L6 ["6. Trade Management & Telemetry - risk/, execution/, telemetry/"]
    slLadder["custom_stoploss -> stoploss_ladder (+18/+30/+50/+80/+120% ROE, 24h timeout)"]
    exitGate["confirm_trade_exit -> exit_policy.confirm_exit_allowed (ROI >= 50% 1H EMA21 guard)"]
    fillHook["order_filled -> Trade custom_data (mtf_candidate_id) + TradeLedger"]
    ledgers["JSONL Ledgers (deque maxlen=2000 + xoay vong 10 MiB .1)"]
    fillHook --> ledgers
    riskAuth --> ledgers
  end

  L1 --> L2
  candOut --> L3
  hunterOff -.->|"disabled on live"| L3
  shortPol -->|"is_valid=True"| L4
  wfPol -->|"is_valid=True"| L4
  longPol -->|"is_valid=True"| L4
  regMem --> sizing --> confirm
  riskAuth -->|"is_approved=True"| L6
```

| Đường chạy | Khi nào dùng | Các tầng thực thi |
|---|---|---|
| **Fast Path** | Mặc định khi sensor khớp bộ live | Parts → Vectorized AND (+ `MomentumRadar` nếu `enable_waterfall`) → `TradeCandidate` → Policy |
| **Event Path** | Fallback hoặc khi dùng `SensorSuite` tuỳ biến | SensorSuite → Builder → SetupComposer → Policy |

Fast Path cố tình bỏ qua `CandidateBuilder` / `SetupComposer` để backtest 5 năm nhanh, nhưng hợp đồng đầu ra giống hệt nhau (được khoá bằng `tests/strategy/test_mtf_candidate_parity.py`).

---

## 2. Policy: Quyền Vào Lệnh (`is_valid`) vs Nhãn Chất Lượng (`quality_grade`)

| Trường | Ý nghĩa thực tế |
|---|---|
| `PolicyEvaluationResult.is_valid` | Điều kiện **đủ chuẩn vào lệnh** (cho phép đi tiếp tới cooldown / `enter_*`) |
| `quality_grade` | Nhãn đánh giá tham khảo — **không** loại các setup Sniper đã đủ 4 phần |
| Waterfall anomaly (`ShortPolicy`) | Kiểm tra 2 chế độ + bộ lọc chân không; khi `short_score > 0`, sàn điểm `base = max(0.70, short_score/100)` để tránh kèo Waterfall hợp lệ bị loại oan |
| Decision Gate | Đọc chuỗi 32 nến + điểm vĩ mô — **không** dùng `quality_grade` để xét duyệt |

---

## 3. Xác Nhận Lệnh, Tính Size, Rủi Ro & Vòng Đời Lệnh

```mermaid
flowchart LR
  pop["populate_entry_trend"] -->|"enter_* + registry"| size["leverage / custom_stake_amount"]
  size -->|"amount, rate"| conf["confirm_trade_entry"]
  conf -->|"guards + JEV + equity"| auth["RiskEngine.authorize_entry"]
  auth -->|"is_approved"| fillIn["order_filled (entry)"]
  fillIn -->|"luu mtf_candidate_id"| hold["Vi The Dang Mo"]
  hold -->|"moi loop / nen"| sl["custom_stoploss (ladder)"]
  hold -->|"kiem tra ROI"| ex["confirm_trade_exit"]
  sl --> fillOut["order_filled (exit)"]
  ex --> fillOut
  fillOut -->|"MFE/MAE + PnL"| tlog["TradeLedger"]
```

**`RiskEngine`** phục vụ hai điểm nối khác nhau của Freqtrade (xem thêm `DATA_CONTRACTS.md` §3.5):

| Bề mặt | Việc nó làm | Việc nó **không** làm |
|---|---|---|
| `confirm_trade_entry` → `authorize_entry` → `RiskAuthorization` | Kiểm tra bảo vệ danh mục + JEV Decision; ghi log `est_stake`, `wallet_equity`, `ai_confidence`, `market_regime`, `policy_score`; trả **`is_approved`** | Không trực tiếp thay đổi khối lượng hoặc đòn bẩy của lệnh đặt |
| `leverage()` / `custom_stake_amount()` | Trả giá trị Freqtrade áp dụng thật vào lệnh (`get_leverage` / `get_stake_amount`) | Không tính size theo khoảng cách Stoploss (ATR/SL distance sizing) |
| `custom_stoploss()` | Điều chỉnh SL theo thang ROE (`+18/+30/+50/+80/+120%`) & cắt sau 24h âm | Không đặt SL cứng trên sàn ngoài callback Freqtrade |

**Phân tầng cấu hình (`config_resolve.py`):**
- Mặc định Python: `enable_dynamic_sizing=False` (`1.0x`).
- Khi bật `enable_dynamic_sizing=true` (trong `user_data/config.json` & `docker/config.mtf_sniper.json`): áp dụng `tag_multipliers` (`_TAG_MULTIPLIERS`) $\times$ `resolve_counter_1d_scale(..., counter_1d_stake_scale)` (mặc định **`0.50`**, có thể tối ưu bằng Hyperopt hoặc env `COUNTER_1D_STAKE_SCALE`), chặn trên bởi `max_stake`; đòn bẩy `min(default_leverage, max_leverage)` (mặc định `10.0`).
- Các tham số vào/lọc/sizing và runtime đều được chụp thành `EffectiveConfig` và băm ra `fingerprint()` khi khởi động.

---

## 4. Vòng Đời `candidate_id`

```text
populate → lọc cooldown / xung đột → CandidateRegistry.remember (chỉ giữ nến sống sót)
confirm / order_filled → tìm trong registry (tối đa 6h)
                      → nếu thiếu: tái tạo từ enter_* gần nhất (tối đa 6h)
                      → nếu vẫn không có: gắn candidate_id_missing
khớp vào lệnh → lưu Trade.set_custom_data(mtf_candidate_id) + giữ map RAM
khớp thoát lệnh → ưu tiên đọc custom_data → ghi TradeLedger (deque + xoay vòng 10 MiB)
```

Tuyệt đối không tự chế ID giả sai định dạng. ID đọc từ DB phải khớp đúng cặp tiền và chiều (`pair` / `side`) của lệnh.

---

## 5. Bản Đồ Module

| Tầng | Vai trò | Đường dẫn |
|---|---|---|
| Triết lý | Bản chất edge, phễu 4 khung, lý do thiết kế từng lớp | `docs/PHILOSOPHY.md` |
| Market | Chỉ báo đa khung (`1D`/`4H`/`1H`/`15m`) & data readiness guards | `market/indicators.py`, `market/data_guards.py` |
| Sensors | Tín hiệu thành phần + `WaterfallMomentumSensor` (`scan_mask` / `scan`) | `signals/sensor_suite.py`, `signals/sensors/` |
| Builder | Gom / khử xung đột cho sensor sự kiện thuần (`scan_mask() is None`) | `signals/candidate_builder.py` |
| Composer | Single-Truth recipe matcher (`compose_suite` mask + event fallback) | `directional/setup_composer.py`, `setup_recipes.py` |
| Policy | Hợp lệ `short_sniper` / `long_sniper` + tách riêng `WaterfallShortPolicy` | `directional/*/policy.py`, `directional/short/waterfall_policy.py` |
| Entry | Điều phối `SetupComposer.compose_suite` → cooldown + registry (`LINT029`) | `execution/entry_pipeline.py` |
| Decision | Native / External Hybrid (`fail_policy` đồng bộ) | `intelligence/decision_adapter.py`, `jev_decision.py` |
| Risk | Guards (CB/DirLock/Distress), sizing `0.50x`, thang SL | `risk/portfolio_protection.py`, `position_sizing.py`, `stoploss_ladder.py`, `risk_engine.py` |
| Exit | Hoãn chốt ROI khi giữ trend 1H (`confirm_exit_allowed`) | `execution/exit_policy.py` |
| Telemetry | Sổ nhật ký JSONL giới hạn RAM + xoay file 10 MiB | `telemetry/decision_ledger.py`, `trade_ledger.py`, `jsonl_io.py` |
| Sync | Tự làm mới ví/vị thế live mỗi `15s` (`bot_loop_start`) | `strategy_mtf_sniper.py` (`exchange_sync_interval_secs`) |
| Identity | Canonical ID + registry bền vững qua restart | `core/candidate_id.py`, `candidate_registry.py` |
| Config | RuntimeMode fail-fast + chuẩn hoá 3 tầng + fingerprint | `core/runtime_mode.py`, `config_resolve.py` |

---

## 6. Nguyên Tắc Bất Di Bất Dịch & Ràng Buộc Kiến Trúc Bằng Code

### 6.1 Ràng buộc tự động (CLI + AST Lint + Typecheck + Docker Build)
Kiến trúc **Sensor đơn lẻ → CandidateBuilder → Đa Sub-system trong `directional/`** được khoá cứng bằng code & kiểm tra tự động qua `.git/hooks/pre-commit`, `docker/Dockerfile.custom`, và CLI:

```bash
# Kiểm tra toàn bộ ranh giới tầng + liệt kê registry các sub-system + chạy luôn pytest:
.venv/bin/python scripts/lint_guardrails.py --verify-all
```

| Mã kiểm tra | Ràng buộc được khoá cứng trong code |
|---|---|
| `LINT018` | Mọi concrete `*Sensor` trong `signals/sensors/` bắt buộc kế thừa `BaseSensor` |
| `LINT020` | `signals/` không được import ngược lên `execution/`, `risk/`, `intelligence/`, `strategy_mtf_sniper` |
| `LINT021` | Sensor không bao giờ được ghi/đụng chuỗi tín hiệu Freqtrade (`enter_long`, `enter_short`, `exit_*`) |
| `LINT022` | Cấm bóp `SetupComposer` về map 1-1 theo chiều (`_by_side` / `{r.side: r}`); phải dùng `recipes_grouped_by_side` |
| `LINT023` | `directional/` giữ tách biệt `long` ↔ `short` (`LINT003`) và cấm import `execution/` / `risk/` / `intelligence/` |
| `LINT024` + Pyrefly | Kiểm tra `SUBSYSTEM_REGISTRY`, `all_live_recipes`, và `DirectionalPolicyProtocol` |
| `LINT025` | 1 Sub-system = 1 Policy class riêng (`LongPolicy`, `ShortPolicy`, `WaterfallShortPolicy`); tự import & kiểm tra `DirectionalPolicyProtocol` |
| `LINT026` | Bắt buộc `DirectionalTradingEngine.subsystem_policies` điều phối theo registry & `EntryPipeline` gọi `SetupComposer.compose_suite()` |
| `LINT027` | Cấm `EntryPipeline` gắn `"subsystem"` lậu không có trong `SUBSYSTEM_REGISTRY` |
| `LINT028` | Bắt buộc mọi `sensor_name` trong `SetupRecipe.required_sensors` phải tồn tại dưới dạng `BaseSensor` thật trong `signals/sensors/` |
| `LINT029` | Cấm tái lập Fast Path (`_candidates_from_sniper_masks`, `MomentumRadar`, `cached_sniper_parts`, `TradeCandidate()`) trong `execution/entry_pipeline.py` |

### 6.2 Quy trình chuẩn khi bổ sung 1 Sub-System mới (Plug-and-Play)
1. **Tạo Sensor đơn lẻ (`signals/sensors/`):** kế thừa `BaseSensor`, bắn `TriggerEvent` cho tín hiệu nguyên tử (tuyệt đối không viết rule portfolio hay `enter_*`).
2. **Tối ưu tốc độ Backtest / Hyperopt (khuyến nghị):** cài đặt thêm `scan_mask()` + `scan_mask_features()` bằng NumPy ngay trên Sensor đó để `SetupComposer.compose_suite()` cắt bằng mask ở tầng C thay vì cấp phát `TriggerEvent`.
3. **Đăng ký Recipe & Spec (`directional/setup_recipes.py`):** thêm `SetupRecipe` (tập `required_sensors`, `side`, `subsystem`, `priority`) và `SubSystemSpec` vào `SUBSYSTEM_REGISTRY`.
4. **Cài bộ lọc/đánh giá (`directional/<side>/`):** triển khai hoặc mở rộng evaluator tuân theo `DirectionalPolicyProtocol` → trả về `PolicyEvaluationResult`.
5. **Chạy cổng kiểm tra:** `.venv/bin/python scripts/lint_guardrails.py --verify-all`.

### 6.3 Checklist không được làm hỏng
- Mọi tín hiệu sống bắt buộc đi qua **một đường Single Truth duy nhất** (`SensorSuite` → `SetupComposer.compose_suite` → `DirectionalTradingEngine`); mọi tối ưu vector hoá giữ parity xanh (`tests/strategy/test_mtf_sniper_mask_parity.py`, `test_mtf_candidate_parity.py`).
- Thành phần rời rạc $\neq$ mở lệnh (`enter_*`).
- `CandidateRegistry.remember` chỉ lưu các ứng viên đã sống sót qua `apply_cooldown` và khử xung đột Long/Short.
- Circuit Breaker phải reset chuỗi thua sau khi kích hoạt nghỉ 72h và gộp trùng các lệnh BTC+ETH thua cùng sóng.
- Giữ Hunter tắt khỏi pipeline live trừ khi có dự án đánh giá lại riêng.
- Mọi hợp đồng trong `DATA_CONTRACTS.md` phải khớp với `core/contracts.py`.
