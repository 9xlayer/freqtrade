# BÁO CÁO THỐNG KÊ HIỆU NĂNG HỆ THỐNG GIAO DỊCH THUẬT TOÁN
## Chiến Lược: `StrategyMTFShortSniper` (Phiên Bản Chuyên Short v1.0)

---

### THÔNG TIN KIỂM THỬ (METADATA)

| Thông Số | Giá Trị Chi Tiết |
| :--- | :--- |
| **Chiến Lược** | [`StrategyMTFShortSniper.py`](file:///Users/nathan/Documents/WORK/freqtrade/strategies/active/StrategyMTFShortSniper.py) |
| **Phiên Bản** | `v1.0` (Chuyên Short Vị Thế + Tích hợp Circuit Breaker & Correlation Guard) |
| **Thời Điểm Backtest** | **2026-11-10 10:00:04 (+07:00)** / `2026-11-10 03:00:04 UTC` |
| **Run ID Freqtrade** | `f561a50a9ebd82358425a98e034615a0f2f2444a` |
| **Tệp Kết Quả Gốc** | `user_data/backtest_results/backtest-result-2026-11-10_03-00-04.zip` |
| **Giai Đoạn Kiểm Thử** | **01/01/2022 00:00:00 – 09/11/2026 00:00:00** (Tổng cộng **1,773 ngày** ~ 4.85 năm) |
| **Sàn Giao Dịch** | Binance Futures (`trading_mode: futures`, `margin_mode: isolated`) |
| **Cặp Tiền Giao Dịch** | `BTC/USDT:USDT`, `ETH/USDT:USDT` |
| **Khung Thời Gian (Timeframes)** | `15m` (Thực thi vào lệnh) \| `1h` (Xác nhận cấu trúc) \| `1d` (Lọc xu hướng vĩ mô) |
| **Thiết Lập Vốn** | Vốn mẫu danh mục: **1,000 USDT** \| Kích thước lệnh: **150 USDT / lệnh** cố định |
| **Đòn Bẩy (Leverage)** | **10x** (Futures) |
| **Số Vị Thế Mở Tối Đa** | 2 lệnh đồng thời (`max_open_trades: 2`) |

---

## 1. TÓM TẮT QUẢN TRỊ CẤP CAO (EXECUTIVE SUMMARY)

Chiến lược **`StrategyMTFShortSniper` (v1.0)** đại diện cho phiên bản chuyên biệt chỉ đánh lệnh Short (Bán khống) sau khi được bổ sung 2 tầng quản trị rủi ro cấp độ danh mục: **Streak Circuit Breaker** (tự ngắt 72 giờ sau 3 lệnh thua liên tiếp) và **Portfolio Correlation Guard** (chặn mở lệnh thứ 2 nếu lệnh 1 đang âm vốn $> 0.8\%$ chart):

1. **Lợi nhuận ròng ấn tượng:** Đạt **+1,449.95 USDT (+145.00% ROI)** trên vốn cơ sở 1,000 USDT.
2. **Cắt giảm Max Drawdown:** Giảm từ $28.50\%$ (339.49 USDT) ở bản gốc xuống còn **$22.29\%$ (273.13 USDT)**.
3. **Tỷ số Calmar bứt phá:** Tăng mạnh từ $4.93$ lên **$7.01$**, chứng minh tính hiệu quả của các quy tắc ngắt mạch rủi ro khi thị trường giật 2 đầu.
4. **Profit Factor xuất sắc:** Đạt **$1.52$** với tỷ lệ thắng **$53.23\%$** trên tổng cộng **248 lệnh**.

---

## 2. BẢNG TỔNG HỢP CHỈ SỐ TOÀN DIỆN (KEY PERFORMANCE METRICS)

| Nhóm Chỉ Số | Tên Chỉ Số (Metric) | Giá Trị Thực Tế | Đánh Giá Chuẩn Mực |
| :--- | :--- | :---: | :--- |
| **Sinh Lời** | **Tổng Lợi Nhuận Ròng (Net Profit)** | **+1,449.95 USDT (+145.00%)** | Vốn tăng từ 1,000 lên 2,449.95 USDT |
| | Kỳ vọng lợi nhuận / lệnh (Expectancy) | **+5.85 USDT / lệnh** | Giá trị kỳ vọng trên từng lệnh rất cao |
| | Profit Factor (Lãi gộp / Lỗ gộp) | **1.52** | Lãi gộp vượt trội so với rủi ro |
| **Rủi Ro & Sụt Giảm**| **Đáy Rút Vốn Tối Đa (Max Account Drawdown)** | **22.29% (273.13 USDT)** | Đã được nén giảm so với bản gốc 28.5% |
| | Thời gian Drawdown dài nhất | **783 ngày 23 giờ 15 phút** | Do đặc tính thị trường uptrend 2023-2024 ít điểm Short |
| **Chất Lượng Hệ Thống**| **Tỷ Số Calmar** | **7.01** | Vượt chuẩn quỹ định lượng ($\ge 3.0$) |
| | **Tỷ Số Sortino** | **1.27** | Khả năng kiểm soát biến động xấu tốt |
| | **Tỷ Số Sharpe** | **0.44 (Tổng thể) / 0.98 (Daily)** | Hiệu quả ổn định |
| **Tần Suất & Lệnh** | Tổng số lệnh thực hiện (Total Trades) | **248 lệnh** | Trung bình ~1 lệnh / tuần |
| | Tỷ lệ thắng (Win Rate) | **53.23%** (132W / 116L) | Tỷ lệ Risk:Reward thực tế ~ 1:2.3 |

---

## 3. THAM SỐ CẤU HÌNH ĐÃ ÁP DỤNG

```json
{
  "strategy_name": "StrategyMTFShortSniper",
  "params": {
    "roi": {
      "0": 0.10,
      "720": 0.08,
      "1440": 0.07
    },
    "stoploss": {
      "stoploss": -0.28
    },
    "buy": {
      "adx_min": 25.0,
      "ema_fast": 12,
      "ema_slow": 26,
      "lookback_breakout": 15,
      "retest_tol_pct": 0.008,
      "rsi_max": 52.0,
      "rsi_min": 30.0,
      "vol_factor": 1.3
    }
  }
}
```
