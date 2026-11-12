# BÁO CÁO TỐI ƯU HÓA HIỆU NĂNG BULL RUN & HYPEROPT
## Chiến Lược: `StrategyMTFSniper` (Phiên Bản v2.1 Bull Hyperopt)

---

### THÔNG TIN TỔNG QUAN KIỂM THỬ (METADATA)

| Thông Số | Giá Trị Chi Tiết |
| :--- | :--- |
| **Chiến Lược** | [`StrategyMTFSniper.py`](file:///Users/nathan/Documents/WORK/freqtrade/strategies/active/StrategyMTFSniper.py) |
| **Phiên Bản** | `v2.1` (Bull Hyperopt: Tối ưu nhánh Long cho chu kỳ Bull Run, nâng cấp Bull Trailing Stoploss đa tầng, Hyperopt 150 Epochs Calmar) |
| **Thời Điểm Backtest** | **2026-11-12 11:03:51 (+07:00)** / `2026-11-12 04:03:51 UTC` |
| **Run ID Freqtrade** | `backtest-result-2026-11-12_03-52-45.zip` |
| **Giai Đoạn Kiểm Thử** | **01/01/2022 00:00:00 – 09/11/2026 00:00:00** (Tổng cộng **1,773 ngày** ~ 4.85 năm) |
| **Dữ Liệu Làm Ấm (Warmup)** | Dữ liệu nến đầy đủ từ 2020. `startup_candle_count = 400` nến |
| **Sàn Giao Dịch** | Binance Futures (`trading_mode: futures`, `margin_mode: isolated`) |
| **Cặp Tiền Giao Dịch** | `BTC/USDT:USDT`, `ETH/USDT:USDT` |
| **Khung Thời Gian (Timeframes)** | `15m` (Thực thi vào lệnh) \| `1h` (Xác nhận cấu trúc) \| `4h` & `1d` (Lọc xu hướng vĩ mô) |
| **Thiết Lập Vốn** | Vốn mẫu danh mục: **1,000 USDT** \| Kích thước lệnh: **75 USDT / lệnh** cố định |
| **Đòn Bẩy (Leverage)** | **10x** (Futures) |
| **Số Vị Thế Mở Tối Đa** | 2 lệnh đồng thời (`max_open_trades: 2`) |

---

## 1. MỤC TIÊU VÀ NGUYÊN TẮC CẢI TIẾN

1. **Vấn đề của phiên bản cũ (v2.0):**
   * Nhánh Long bị gò bó quá mức: Cooldown 42 nến (10.5h) quá dài khiến bot bỏ lỡ sóng tiếp diễn trong chu kỳ Bull; Khoảng cách EMA 21 bị giới hạn 1.0% khiến các nến breakout thân to uy lực bị loại bỏ oan; Trần RSI 1H giới hạn ở 48.9 khiến bot chỉ dám Long khi giá giảm rất sâu (thường là gãy trend hơn là nhịp hồi lành mạnh).
   * Cơ chế Stoploss dời quá sớm: Khóa lãi sớm ở vùng +3.5% - +5% chart khiến các lệnh Long không thể "thở" để chạy hết biên độ target ROI +70% đến +100%.
2. **Giải pháp thực hiện (v2.1):**
   * **Nâng cấp Custom Stoploss Ladder (Bull Trailing V2):**
     * $+2.0\%$ chart ($+20\%$ ROE) $\rightarrow$ Dời SL về Breakeven $+0.2\%$ chart ($+2\%$ ROE).
     * $+3.5\%$ chart ($+35\%$ ROE) $\rightarrow$ Khóa $+1.0\%$ chart ($+10\%$ ROE).
     * $+5.0\%$ chart ($+50\%$ ROE) $\rightarrow$ Khóa $+2.5\%$ chart ($+25\%$ ROE).
     * $+8.0\%$ chart ($+80\%$ ROE) $\rightarrow$ Cho lệnh thở với khoảng đệm trailing 3.0% chart (30% ROE từ đỉnh cao nhất).
     * $+12.0\%$ chart ($+120\%$ ROE) $\rightarrow$ Siết chặt trailing 2.0% chart (20% ROE từ đỉnh cao nhất).
   * **Chạy Hyperopt 150 Epochs (`CalmarHyperOptLoss`):**
     * Tìm kiếm tổ hợp tham số vào lệnh tối ưu nhất cho Long, vừa tối đa hóa PnL vừa triệt tiêu rủi ro sụt giảm vốn Max Drawdown.
     * Khóa cứng toàn bộ tham số Short đã kiểm chứng để bảo toàn khả năng kiếm tiền mùa gấu.

---

## 2. BẢNG SO SÁNH HIỆU NĂNG TỔNG THỂ

| Chỉ Số Đánh Giá | Phiên Bản Cũ (v2.0 Baseline) | **Phiên Bản v2.1 (Hiện Tại)** | Mức Cải Thiện |
| :--- | :---: | :---: | :---: |
| **Tổng Lợi Nhuận Ròng (Total Net Profit)** | **+806.25 USDT (+80.62%)** | **+1,217.51 USDT (+121.75%)** | **+51.0% lợi nhuận** 🚀 |
| **Lợi Nhuận Nhánh LONG** | **+216.60 USDT** (trước V2: +188 USDT) | **+605.90 USDT** | **+180% lợi nhuận Long** 💥 |
| **Lợi Nhuận Nhánh SHORT** | **+589.65 USDT** | **+611.60 USDT** | Ổn định và vững chắc |
| **Tổng Số Lệnh (Trades)** | 208 lệnh | **233 lệnh** | +25 lệnh chất lượng cao |
| **Tỷ Lệ Thắng (Win Rate)** | 52.4% (109W / 99L) | **56.7% (132W / 101L)** | **+4.3% WR** |
| **Mức Sụt Giảm Vốn Tối Đa (Max Drawdown)** | **12.15% (134.24 USDT)** | **6.62% (72.02 USDT)** | **Giảm 46% Drawdown** 🛡️ |
| **Thời Gian Drawdown Dài Nhất** | 301 ngày | **297 ngày** | Rút ngắn 4 ngày |
| **Chuỗi Thua Tối Đa (Max Consec. Losses)** | 9 lệnh | **6 lệnh** | Giảm 33% rủi ro chuỗi thua |
| **Tỷ Số Calmar (Freqtrade Daily)** | 7.16 | **19.82** | **Tăng gấp 2.76 lần** |
| **Tỷ Số Sharpe** | 1.22 | **1.57** | Tăng mạnh |
| **Tỷ Số Sortino** | 1.10 | **1.68** | Tăng mạnh |

---

## 3. PHÂN TÍCH THEO NĂM (YEARLY BREAKDOWN)

Hệ thống đạt trạng thái lý tưởng: **Tất cả các năm đều có lợi nhuận dương**, không có năm nào bị lỗ ròng:

| Năm | Tổng Số Lệnh | Win Rate | PnL Tổng (USDT) | PnL Nhánh Long | PnL Nhánh Short | Đánh Giá Tình Hình Thị Trường |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **2022** | 26 | 57.7% | **+72.69 USDT** | -37.67 USDT | +110.36 USDT | Mùa gấu sập đổ (LUNA/FTX), Short gánh toàn bộ danh mục |
| **2023** | 31 | 48.4% | **+65.66 USDT** | **+127.61 USDT** | -61.95 USDT | **Biến năm thua lỗ (-92.76$) thành năm có lãi** |
| **2024** | 79 | 51.9% | **+344.04 USDT** | **+320.40 USDT** | +23.64 USDT | **Pha Bull Run: Long ăn trọn sóng lớn, PnL gấp 3.2 lần** |
| **2025** | 59 | 59.3% | **+395.71 USDT** | **+198.82 USDT** | +196.89 USDT | Thị trường 2 chiều, Long và Short đóng góp đều 50-50 |
| **2026** | 38 | 68.4% | **+339.41 USDT** | -3.25 USDT | +342.66 USDT | Short ăn đậm các nhịp phân phối đỉnh |
| **TỔNG** | **233** | **56.7%** | **+1,217.51 USDT** | **+605.90 USDT** | **+611.60 USDT** | **Cân bằng hoàn hảo giữa Long và Short** |

---

## 4. CHI TIẾT THAM SỐ TỐI ƯU HÓA (PARAMETER SPECIFICATION)

```json
{
  "strategy_name": "StrategyMTFSniper",
  "params": {
    "roi": {
      "0": 1.0,
      "720": 0.7
    },
    "stoploss": {
      "stoploss": -0.28
    },
    "trailing": {
      "trailing_stop": false,
      "trailing_stop_positive": null,
      "trailing_stop_positive_offset": 0.0,
      "trailing_only_offset_is_reached": false
    },
    "max_open_trades": {
      "max_open_trades": 2
    },
    "buy": {
      "block_1d_counter_wave": true,
      "block_4h_counter_wave": true,
      "min_adx_4h": 18.0,
      "short_candle_body_ratio_min": 0.62,
      "short_cooldown_candles": 96,
      "short_loc_tolerance_1h": 0.991,
      "short_lookback_support": 17,
      "short_max_dist_ema21": 1.0,
      "short_min_1h_rsi_exhaustion": 55.1,
      "short_min_rsi_entry": 27.8,
      "long_block_1d_macd": true,
      "long_candle_body_ratio_min": 0.63,
      "long_cooldown_candles": 18,
      "long_loc_tolerance_1h": 1.008,
      "long_lookback_resistance": 22,
      "long_max_1h_rsi_exhaustion": 62.4,
      "long_max_dist_ema21": 3.3,
      "long_max_rsi_entry": 67.6,
      "long_overextended_dist_max": 0.59
    }
  }
}
```

---

## 5. HƯỚNG DẪN TRIỂN KHAI LÊN PRODUCTION (PI DEPLOYMENT)

Để triển khai bản cập nhật này lên Raspberry Pi Production khi sẵn sàng:
1. Push commit từ Local Mac lên nhánh `develop` trên Git Remote:
   ```bash
   git push origin develop
   ```
2. Trên Raspberry Pi (`pi@pi.local`):
   ```bash
   cd /freqtrade # hoặc thư mục repo trên pi
   git pull origin develop
   docker compose restart freqtrade
   ```
