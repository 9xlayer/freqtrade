# MTF Sniper — Mục Lục Tài Liệu Kỹ Thuật (`docs/`)

**Package:** `strategies/mtf_sniper/` · **Version:** `4.7.0-1w-regime` (`STRATEGY_VERSION` trong `__init__.py`) · **Decision Engine:** `2.7.2`  
**Cặp giao dịch:** `BTC/USDT:USDT`, `ETH/USDT:USDT` (Futures Isolated `10x`) · **Đa khung:** `1W · 1D · 4H · 1H · 15m`

Thư mục này lưu giữ trọn vẹn toàn bộ thiết kế chiến lược, kiến trúc phân lớp, hợp đồng dữ liệu và cơ chế khoá code chống AI Slop của **MTF Sniper** trực tiếp trong Git (đồng bộ 100% với bản Canvas kiến trúc và được kiểm tra tự động bởi quy tắc `LINT036`).

---

## 1. Bộ Tài Liệu Chuẩn Hiện Hành (Authoritative — Khoá Tự Động Bởi `LINT036`)

Đọc theo đúng thứ tự từ trên xuống khi tiếp cận hệ thống hoặc trước khi chỉnh sửa code:

| Thứ tự | Tài liệu | Nội dung cốt lõi | Khi nào cần đọc |
|---|---|---|---|
| **1** | [`PHILOSOPHY.md`](./PHILOSOPHY.md) | Bản chất lợi thế đuôi phải (convex payoff), **Phễu lọc 5 khung (`1W -> 1D -> 4H -> 1H -> 15m`)**, so sánh hiệu chỉnh riêng **BTC vs ETH (`custom_pair_params`)**, thang khoá lãi 5 bậc ROE và triết lý bảo vệ danh mục | Khi muốn hiểu *tại sao* một điều kiện lọc, hệ số hạ vốn hay bậc nâng SL lại tồn tại |
| **2** | [`ARCHITECTURE.md`](./ARCHITECTURE.md) | **Luồng thực thi 8 chặng** từ Thin Facade `StrategyMTFSniper` tới JSONL Ledgers, bảng `SUBSYSTEM_REGISTRY`, ma trận `RuntimeMode`, **công thức chấm điểm JEV Hybrid + SQLite WAL cache**, và vòng đời `candidate_id` | Khi cần nắm luồng gọi hàm, ranh giới module, cấu hình Docker/Backtest, hoặc thêm Sub-System mới |
| **3** | [`DATA_CONTRACTS.md`](./DATA_CONTRACTS.md) | Toàn bộ Enums (`TradeSide`, `SensorType`, `MarketRegime`, `DecisionAction`, `RejectionReasonCode`), định dạng chuẩn `candidate_id`, và 7 hợp đồng `@dataclass(frozen=True)` liên tầng | Khi làm việc với dữ liệu truyền giữa Sensor, Builder, Composer, Policy, RiskEngine và Telemetry |
| **4** | [`GUARDRAILS.md`](./GUARDRAILS.md) | **Kiến trúc Guardrails 5 vòng bảo vệ chống AI Slop (`scripts/guardrails/`)**, **Ma trận Allowlist DAG 8 tầng**, bảng tra cứu đầy đủ **`LINT001`–`LINT036`**, Cursor Hooks tức thời `<150ms`, Git `pre-commit` và cổng chặn `Dockerfile.custom` | Bắt buộc nắm rõ khi viết code mới, refactor, hoặc mở rộng quy tắc kiểm soát tự động |

---

## 2. Hồ Sơ Vận Hành & Lịch Sử Chuyển Đổi (Reference / Archive)

Các tài liệu dưới đây lưu lại bối cảnh kỹ thuật của các đợt ổn định hoá và thực nghiệm lịch sử:

| Tài liệu | Vai trò |
|---|---|
| [`LIVE_READINESS_CHECKLIST.md`](./LIVE_READINESS_CHECKLIST.md) | **Checklist bắt buộc trước khi bật tiền thật (`dry_run: false`)**: cài đặt Binance Futures (`One-way`, `Single-Asset`, `MIN_NOTIONAL >= 500 USDT`), dọn sạch `tradesv3.sqlite` Dry-Run, bật `stoploss_on_exchange`, đổi `price_side: "other"`, khoá trần `risk_per_trade_pct` và kỷ luật Kill-Switch |
| [`STABILIZATION_DETERMINISTIC.md`](./STABILIZATION_DETERMINISTIC.md) | Tổng hợp 15 hạng mục gia cố tính tất định (fail-closed `NaN`/`Inf`, khử trùng CB, chuẩn hoá UTC, tách bạch cổng duyệt vs sizing) và các giới hạn vận hành thực tế còn lưu ý |
| [`MIGRATION_PLAN.md`](./MIGRATION_PLAN.md) | Lộ trình tiến hoá từ bản đơn khối ban đầu (`P0`–`P4`) sang kiến trúc Single-Truth `SensorSuite -> SetupComposer -> SUBSYSTEM_REGISTRY` hiện nay |
| [`P4_LONG_HUNTER_EVIDENCE.md`](./P4_LONG_HUNTER_EVIDENCE.md) | Bằng chứng thực nghiệm lịch sử (trên tag `v3.2.0-p4-hunter-gates`) giải thích vì sao nhánh `JevHunter` Long Breakout bị đóng cổng và gỡ bỏ khỏi hệ thống |

---

## 3. Lệnh Kiểm Chứng Nhanh

```bash
# Chạy nhanh (<150ms): AST + Ma trận DAG + Chống AI Slop + SUBSYSTEM_REGISTRY + Đồng bộ Docs (LINT036)
.venv/bin/python scripts/lint_guardrails.py --fast

# Cổng kiểm định đầy đủ trước khi commit: LINT001-LINT036 + Pyrefly + Ruff + toàn bộ pytest chiến lược
.venv/bin/python scripts/lint_guardrails.py --verify-all
```
