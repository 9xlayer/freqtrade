# MTF Sniper — Hợp Đồng Dữ Liệu (Data Contracts)

**Package:** `strategies/mtf_sniper/` · **Nguồn gốc code:** `core/contracts.py`, `core/enums.py`, `core/candidate_id.py`  
**Version:** khớp `STRATEGY_VERSION` trong `__init__.py` (`4.6.0-counter1d`)

Tài liệu này mô tả đúng **các schema đã triển khai trong code**. Mọi thiết kế mục tiêu trong tương lai nếu nhắc tới sẽ được đánh dấu rõ là chưa chạy thực tế.

Tất cả dataclass liên tầng đều dùng `frozen=True` (**bất biến nông — shallow immutability**): không thể gán lại field trên dataclass, nhưng `List` / `Dict` lồng bên trong vẫn cần các module gọi coi là dữ liệu chỉ đọc.

---

## 1. Enumerations (`core/enums.py`)

| Enum | Giá trị |
|---|---|
| `TradeSide` | `long`, `short` |
| `SensorType` | `anomaly`, `pattern` |
| `MarketRegime` | `bull_expansion`, `bull_pullback`, `bear_capitulation`, `bear_relief`, `choppy_ranging`, `volatility_squeeze` |
| `DecisionAction` | `confirm`, `veto`, `hold` |
| `RejectionReasonCode` | Mã từ chối của Sensor/Builder, Directional, JEV AI, Risk (xem trực tiếp `core/enums.py`) |

---

## 2. Định Danh Ứng Viên (`core/candidate_id.py`)

Định dạng chuẩn duy nhất (canonical format):

```text
{pair}_{side}_{utc_epoch}_{candle_index}
```

Ví dụ: `BTC/USDT:USDT_short_1717243200_42`

| Hàm hỗ trợ | Vai trò |
|---|---|
| `make_candidate_id` | Tạo ID chuẩn; kiểm tra hợp lệ `pair` (chứa `/`), `side` chuẩn `long`/`short`, `candle_index` kiểu nguyên $\ge 0$, thời gian `datetime`/`Timestamp` |
| `parse_candidate_id` | Tách và kiểm tra đúng định dạng chuẩn duy nhất |
| `is_canonical_candidate_id` | Parse + tạo lại ID xem có khớp vòng lặp (round-trip) hay không |
| `candidate_id_matches_fields` | Đối chiếu ID với `pair` / `side` / `timestamp` / `candle_index` |
| `candidate_id_matches_trade` | Đối chiếu ID với ngữ cảnh `pair` / `side` của `Trade` |
| `CANDIDATE_ID_MISSING` | Giá trị đánh dấu khi không thể tái tạo lại liên kết |
| `CANDIDATE_ID_CUSTOM_DATA_KEY` | Khóa `"mtf_candidate_id"` lưu bền vững trong custom data của `Trade` |

`CandidateRegistry.remember` tự dựng lại/thay thế mọi `candidate_id` truyền vào nếu không khớp `pair`/`side`/`timestamp`/`candle_index`. Hàm `lookup` bắt buộc phải có `current_time` để giới hạn độ tuổi tín hiệu (mặc định tối đa 6 giờ); `allow_unbounded=True` chỉ dùng để chẩn đoán.

**Các dạng không hợp lệ:** thiếu `candle_index`; `side` viết hoa (`LONG`); epoch hoặc index có số `0` đứng đầu (`01`); index âm; sai cặp tiền; tự chế ID theo tên tag.  
Cửa sổ đối chiếu tối đa: `DEFAULT_LOOKUP_MAX_AGE` = **6 giờ** (dùng chung cho cả `CandidateRegistry.lookup` lẫn đường fallback tái tạo từ DataFrame).

---

## 3. Các Hợp Đồng Dữ Liệu (`core/contracts.py`)

### 3.1 `TriggerEvent` (frozen)

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `sensor_name` | `str` | Ví dụ: `short_structure` |
| `sensor_type` | `SensorType` | |
| `side` | `TradeSide` | |
| `pair` | `str` | |
| `timestamp` | `datetime` | Thời điểm đóng/mở nến |
| `candle_index` | `int` | Vị trí hàng tuyệt đối (`iloc`) trong DataFrame đã phân tích |
| `raw_metrics` | `Dict[str, Any]` | Chỉ số nhanh tại cây nến đó |

### 3.2 `TradeCandidate` (frozen)

| Trường | Kiểu | Ghi chú |
|---|---|---|
| `candidate_id` | `str` | ID chuẩn — xem mục 2 |
| `pair` | `str` | |
| `side` | `TradeSide` | |
| `primary_sensor` | `str` | Ví dụ: `setup:sniper_short` sau composer / fast path |
| `secondary_sensors` | `List[str]` | |
| `confluence_count` | `int` | |
| `timestamp` | `datetime` | |
| `candle_index` | `int` | |
| `raw_features` | `Dict[str, Any]` | |
| `cooldown_bars` | `int` | Mặc định `12` |

Được tạo bởi **Fast Path (vector hóa)** hoặc **Event Path (`SensorSuite -> Builder -> SetupComposer`)** với cùng một cấu trúc hợp đồng (xem `ARCHITECTURE.md`).

### 3.3 `PolicyEvaluationResult` (frozen)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `is_valid` | `bool` | **Quyền hợp lệ của setup** — được phép đi tiếp ra `enter_*` |
| `score` | `float` | Điểm đánh giá chất lượng |
| `quality_grade` | `str` | Nhãn xếp hạng (`A+`/`A`/`B`/`C`/`REJECT`) — chỉ mang tính thông tin tại tầng Policy |
| `reasons` | `List[str]` | Ví dụ: `ACCEPT` |
| `suggested_stop_distance_pct` | `float` | |
| `target_rr` | `float` | |
| `metadata` | `Dict[str, Any]` | Chứa `entry_tag` khi được chấp nhận |

**Bất biến:** Một setup có `is_valid=True` và `quality_grade=REJECT` vẫn đi tới `confirm_trade_entry` bình thường. Decision Gate **không** đọc `quality_grade`.

### 3.4 `DecisionVerdict` (frozen)

| Trường | Kiểu |
|---|---|
| `action` | `DecisionAction` |
| `score` | `float` |
| `confidence` | `float` |
| `veto_reasons` | `List[str]` |
| `model_version` | `Optional[str]` |
| `payload_hash` | `str` |
| `timestamp` | `datetime` |

### 3.5 Rủi Ro: Hai Cơ Chế Tách Biệt (Không Được Nhầm Lẫn)

#### A. `RiskAuthorization` — chỉ là **cổng duyệt lệnh** ở `confirm_trade_entry()`

Do `RiskEngine.authorize_entry()` trả về và được dùng tại `confirm_trade_entry()` đúng bằng:

```text
return bool(auth.is_approved)
```

| Trường | Ý nghĩa thực tế ở bước confirm |
|---|---|
| `is_approved` | **Trường duy nhất Strategy dùng** để cho phép hay từ chối mở lệnh |
| `stake_amount` | Giá trị ước tính để ghi log (`amount * rate / lev`, luôn truyền `enable_dynamic_sizing=False` vào `authorize_entry` để tránh nhân đôi với `custom_stake_amount`); bằng `0.0` nếu thiếu `amount`/`rate` |
| `leverage` | Đòn bẩy mục tiêu (`get_leverage(None)` -> `10.0`) dùng ghi nhận exposure, **không** trực tiếp đặt vào lệnh sàn |
| `stoploss_rate` | Stoploss cặp/mặc định truyền vào để ghi ngữ cảnh |
| `risk_budget_used_pct` / `risk_per_trade_pct` | **`None`** — **chưa** triển khai tính ngân sách rủi ro theo khoảng cách stoploss |
| `wallet_equity` / `total_exposure_pct` | Lấy từ `self.wallets.get_total(stake_currency)` khi có sẵn; bằng **`None`** nếu không có |
| `sizing_method` | Nhãn `"freqtrade_proposed"`; không phải bằng chứng authorize tự phân bổ vốn |
| các trường khác | Thông tin portfolio / fingerprint / exposure — xem `core/contracts.py` |

**Tuyệt đối không mô tả `RiskAuthorization` là bộ phân bổ vốn thật cho lệnh.**

#### B. Các callback Sizing của Freqtrade — nơi thực sự quyết định khối lượng đặt lệnh

| Callback | Strategy → RiskEngine | Hành vi (`risk/position_sizing.py`) |
|---|---|---|
| `leverage()` | `RiskEngine.get_leverage(max_leverage)` | `min(default_leverage, max_leverage)`; mặc định **`10x`** |
| `custom_stake_amount()` | `RiskEngine.get_stake_amount(...)` | Nhận `proposed_stake` từ Freqtrade, chặn trần `max_stake` |
| | | `enable_dynamic_sizing=False` (mặc định Python) → hệ số **1.0** |
| | | Khi `enable_dynamic_sizing=True` (mặc định config Docker/local) → nhân hệ số tag (`_TAG_MULTIPLIERS` / `tag_multipliers`) $\times$ `resolve_counter_1d_scale(..., counter_1d_stake_scale)` (mặc định **`0.50x`** khi ngược 1D), rồi chặn `min(..., max_stake)` |

Thang stoploss vẫn đi riêng qua `custom_stoploss()` → `RiskEngine.get_stoploss()` (`risk/stoploss_ladder.py`).

### 3.6 Nhật Ký Telemetry

**`TelemetryDecisionRecord`** (frozen) — các trường quyết định cơ bản (`ai_verdict`, `ai_confidence`, `ai_model_version`, `market_regime`, `policy_score`) cộng thêm:

- `setup_type`, `signal_source`, `policy_version`
- `config_fingerprint`
- `feature_snapshot` (dict gọn nhẹ — không bao giờ lưu cả DataFrame)

`RiskEngine` khử trùng các lần từ chối giống hệt nhau trong cùng 1 cây nến 15m (`_seen_reject_bursts`) để vòng lặp 5 giây không làm tràn file ledger.

**`TelemetryTradeRecord`** (frozen) — kết quả lệnh + MFE/MAE; `candidate_id` phải là định dạng chuẩn hoặc `candidate_id_missing`.

Cả `DecisionLedger` và `TradeLedger` đều dùng bộ đệm RAM giới hạn `deque(maxlen=2000)` và tự xoay file JSONL sang `<path>.1` khi đạt `10 MiB` (`telemetry/jsonl_io.py`).

---

## 4. Bộ Test Kiểm Tra Hợp Đồng

Các kiểm tra cấu trúc trường dữ liệu và ID chuẩn nằm tại:

- `tests/strategy/test_mtf_data_contracts.py`
- `tests/strategy/test_mtf_candidate_id_hardening.py`
- `tests/strategy/test_mtf_candidate_parity.py`
