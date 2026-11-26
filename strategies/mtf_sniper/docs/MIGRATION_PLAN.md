# MTF Sniper — Lộ Trình Chuyển Đổi Kiến Trúc (Lịch Sử & Hiện Tại)

**Đường dẫn:** `strategies/mtf_sniper/docs/MIGRATION_PLAN.md`  
**Package hiện hành:** `STRATEGY_VERSION` trong `__init__.py` (`4.7.0-1w-regime`)  
**Tài liệu kiến trúc chuẩn hiện nay:** `PHILOSOPHY.md` + `ARCHITECTURE.md` + `STABILIZATION_DETERMINISTIC.md` + `DATA_CONTRACTS.md`

> **Cách đọc tài liệu này:** P0–P4 là **hồ sơ lịch sử chuyển đổi**. Mô tả "luồng live" bên trong các giai đoạn đó phản ánh code **tại thời điểm đó**, không phải hiện tại. **P5+ / Stabilization** mới là kiến trúc đang chạy.

```text
P0 Kiểm toán → P1 Sensors → P2 Parity Engine → P3 Risk/Telemetry → P4 Long Hunter Gates
        → P5 SensorSuite Cutover → Stabilization Deterministic → v4.7.0-1w-regime (Hiện tại)
```

---

## Các Giai Đoạn Lịch Sử (Lưu Trữ)

### Giai đoạn P0 — Kiểm toán & Đóng băng Baseline *(lịch sử)*
- Đóng băng baseline 5 năm ban đầu trên bản chiến lược đơn khối lúc đó.
- Các mốc số thời kỳ đó (như 273 lệnh / +24,282) chỉ là **mốc lưu trữ**, không phải baseline tất định sau Ep46.

### Giai đoạn P1 — Core Contracts & Sensor Suite *(lịch sử, hoàn tất)*
- Tách `core/enums.py`, `core/contracts.py`, các sensor thành phần, `SensorSuite`, `CandidateBuilder`.

### Giai đoạn P2 — Directional Engine & Behavioral Parity *(lịch sử, hoàn tất)*
- Ở thời điểm P2, luồng live vẫn còn mask cũ + hunter_merge + parity helpers; nhánh SensorSuite→Builder→Directional lúc đó mới ở mức thử nghiệm.

### Giai đoạn P3 — Risk Engine & Telemetry *(lịch sử, hoàn tất)*
- Gom các lớp bảo vệ danh mục, ledger ghi nhật ký và tách module thoát lệnh. Risk authorization chỉ đóng vai trò **cổng duyệt lệnh** (không thay thế sizing).

### Giai đoạn P4 — Long Hunter Gates *(lịch sử, đã chặn mặc định)*
- Hồ sơ thực nghiệm nằm tại `P4_LONG_HUNTER_EVIDENCE.md` ở tag **`v3.2.0-p4-hunter-gates`**.
- Mặc định production giữ nguyên `enable_hunter_long=false`.
- **Package hiện hành (`4.6.0-counter1d`)** vẫn tắt Hunter khỏi luồng vào lệnh live bất chấp cờ cấu hình cũ còn sót.

---

## P5 — Cắt Chuyển Sang SensorSuite Live *(nền tảng trực tiếp của bản hiện tại)*

- Loại bỏ phụ thuộc `strategies/active/`; chuyển toàn bộ về package `strategies/mtf_sniper`.
- Mọi tín hiệu vào lệnh đi qua `EntryPipeline`; các adapter JEV nằm gọn trong `intelligence/`.
- Thay thế checklist "đúng 273 lệnh" cũ bằng các baseline backtest mới xác thực đầy đủ.

**Trạng thái hiện hành sau P5 & v4.6.0-counter1d:**

| Hạng mục | Thực tế đang chạy |
|---|---|
| Vào lệnh | Hai đường đồng nhất: Fast Path (vector hóa) **hoặc** Event Path (`SensorSuite -> Builder -> Composer`) → dùng chung Policy |
| Hunter | Tắt cứng trong `EntryPipeline.live` (`hunter_escalation=None`) |
| Decision Gate | Tuỳ chọn `native` ở `deterministic`; Hybrid `external` ở `decision_external` |
| Candidate ID | Chuẩn `{pair}_{side}_{epoch}_{candle_index}`; registry + fallback DF (tối đa 6h); lưu bền vững ở custom data của `Trade` |
| Risk & Sizing | Cổng duyệt + thang SL ROE + giảm `0.50x` ngược 1D (`counter_1d_stake_scale`) + tự làm mới ví sàn live mỗi 15s |
| Baseline mới nhất | `20220101–20261124` (`v4.6.0-counter1d`, `JEV_MODE=native`): `334` lệnh, `+184,121.66 USDT`, `14.42%` Max DD, PF `2.56`, Calmar `1365.48` |

---

## Checklist Xác Thực Live (Hiện Hành)

| Kiểm tra | Yêu cầu |
|---|---|
| 1. Unit / contract tests | Toàn bộ `tests/strategy/` xanh (`444 passed, 1 skipped`) + `scripts/lint_guardrails.py` (`0 violations`) |
| 2. Full BT 5Y | Đối chiếu số lệnh / PnL / Max DD / PF với baseline mới nhất mỗi khi chạm luồng tín hiệu hoặc sizing |
| 3. Docker | `--strategy-path strategies/mtf_sniper` + overlay `mtf_runtime_mode=decision_external` (`fallback_native`, `exchange_sync_interval_secs=15`) |
