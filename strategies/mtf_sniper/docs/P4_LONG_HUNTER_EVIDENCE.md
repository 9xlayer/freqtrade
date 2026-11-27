# P4 Long Hunter — Hồ Sơ Thực Nghiệm & Lý Do Đóng Cổng (2026-11-18)

> **Lưu ý ngữ cảnh phiên bản:** Thí nghiệm này được thực hiện trên tag chiến lược cũ **`v3.2.0-p4-hunter-gates`**. Package hiện hành đã là **`4.7.0-1w-regime`** (xem `__init__.py`), trong đó toàn bộ nhánh `JevHunter` đã được **gỡ bỏ hoàn toàn** khỏi codebase. Đây là tài liệu lưu trữ kết quả thử nghiệm lịch sử giải thích lý do đóng cổng, **không** mô tả hành vi vào lệnh hiện tại.

## Thiết Lập Thí Nghiệm
- Phiên bản lúc thử nghiệm: `strategies/mtf_sniper` `v3.2.0-p4-hunter-gates` (module gộp cũ `execution/hunter_merge.py`)
- Khung thời gian test: `20220101–20261116` (`JEV_MODE=native`)
- Module bộ lọc: `directional/long/hunter_gates.py`

## Baseline Trước Khi Lọc (Bật Thả Cửa Long Hunter)

| Cấu hình | Số lệnh | Lợi nhuận ròng | Ghi chú |
|---|---:|---:|---|
| Parity (`hunter_long=false`) | 273 | **+24,282.26** | Mốc chuẩn đối chiếu |
| Chưa lọc (`hunter_long=true`) | 348 | **+5,964.72** | Tụt −18.3k so với baseline |

### Chi Tiết Nhóm `jev_hunter_breakout_expansion` Chưa Lọc

| Chỉ số | Giá trị |
|---|---|
| Số lệnh | 91 |
| PnL | **−702.05 USDT** |
| Win rate | 54.9% |
| Lệnh thua có MFE < 1% | 28 lệnh / **−2,241 USDT** (mua đuổi / gãy ngay không có lực theo) |
| In-sample (70%) | 63 lệnh / −792.73 USDT |
| Out-of-sample (30%) | 28 lệnh / +90.67 USDT (mẫu quá nhỏ) |

## Bộ Cổng Lọc P4 Đã Cài Đặt (`hunter_gates.py`)

`evaluate_long_hunter_breakout` siết chặt các ngưỡng mặc định:
- `confidence >= 0.90` (trước đây 0.85)
- `upper_wick_ratio <= 0.20` (trước đây 0.285)
- `close_pos_ratio >= 0.72` (trước đây 0.65)
- Khoảng cách `EMA21 <= 0.8%` (trước đây 1.5%)
- `RSI <= 65` (trước đây 72)
- Tỷ lệ thân nến tăng `>= 0.55`
- Động lượng đa khung: `macdhist_4h > 0` **hoặc** `is_bull_wave_4h`
- `dist_from_ema_200_1d <= 0.45`, kèm `long_score >= 50` và các kiểm tra PDH/macro/sideway/vol hiện có.

## Kết Quả Sau Khi Áp Bộ Lọc P4

| Cấu hình | Số lệnh | Lợi nhuận ròng | Lệnh Long Hunter lọt qua |
|---|---:|---:|---:|
| `hunter_long=false` | 273 | **+24,282.26** | 0 |
| `hunter_long=true` + P4 gates | 273 | **+24,282.26** | **0** |

Bộ lọc P4 đã loại sạch 100% các tín hiệu breakout-expansion lịch sử dưới chuẩn chất lượng trên → bật cờ hay tắt cờ cho kết quả y hệt baseline (vô hại với danh mục).

## Kết Luận
- Giữ mặc định production `enable_hunter_long=false` (và ở bản hiện tại `EntryPipeline.live` đã tắt cứng nhánh Hunter escalation).
- Chỉ mở lại khi có bộ phát hiện tín hiệu mới hoặc thiết kế khác chứng minh được kỳ vọng OOS dương rõ rệt mà không ăn vào edge của `long_A80_plus` / `waterfall`.

## Tra Cứu Mã Nguồn Lịch Sử
- Hai tệp `strategies/mtf_sniper/directional/long/hunter_gates.py` và `tests/strategy/test_mtf_sniper_long_hunter_gates.py` đã được dọn bỏ ở bản `v4.7.0-1w-regime` (có thể tra cứu lại nguyên trạng tại git tag `v3.2.0-p4-hunter-gates`).
