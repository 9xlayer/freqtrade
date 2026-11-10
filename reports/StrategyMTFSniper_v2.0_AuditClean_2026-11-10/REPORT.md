# BÁO CÁO KIỂM TOÁN VÀ ĐÁNH GIÁ HIỆU NĂNG THỰC TẾ
## Chiến Lược: `StrategyMTFSniper` (Bản Kiểm Toán Chuẩn Xác v2.0)

---

### THÔNG TIN KIỂM TOÁN KỸ THUẬT (METADATA)

| Thông Số | Giá Trị Chi Tiết |
| :--- | :--- |
| **Chiến Lược** | [`StrategyMTFSniper.py`](file:///Users/nathan/Documents/WORK/freqtrade/strategies/active/StrategyMTFSniper.py) |
| **Phiên Bản** | `v2.0` (Audit Clean: Sửa lỗi kiến trúc, cấm Long Bear 1D, làm ấm 2 năm dữ liệu 1D, loại bỏ bẫy cắt non) |
| **Thời Điểm Backtest** | **2026-11-10 11:04:02 (+07:00)** / `2026-11-10 04:04:02 UTC` |
| **Run ID Freqtrade** | `backtest-result-2026-11-10_04-04-02.zip` |
| **Giai Đoạn Kiểm Thử** | **01/01/2022 00:00:00 – 09/11/2026 00:00:00** (Tổng cộng **1,773 ngày** ~ 4.85 năm) |
| **Dữ Liệu Làm Ấm (Warmup)** | Bổ sung đầy đủ nến 1D, 4H, 1H từ **01/01/2020** (730 ngày làm ấm trước chu kỳ kiểm thử) |
| **Sàn Giao Dịch** | Binance Futures (`trading_mode: futures`, `margin_mode: isolated`) |
| **Cặp Tiền Giao Dịch** | `BTC/USDT:USDT`, `ETH/USDT:USDT` (Cố định whitelist chuẩn) |
| **Khung Thời Gian (Timeframes)** | `15m` (Thực thi vào lệnh) \| `1h` (Xác nhận cấu trúc) \| `1d` (Lọc xu hướng vĩ mô) |
| **Thiết Lập Vốn** | Vốn mẫu danh mục: **1,000 USDT** \| Kích thước lệnh: **150 USDT / lệnh** cố định |
| **Đòn Bẩy (Leverage)** | **10x** (Futures) |
| **Số Vị Thế Mở Tối Đa** | 2 lệnh đồng thời (`max_open_trades: 2` đồng bộ trong code, config, và json) |

---

## 1. BẢN CHẤT THẬT CỦA HỆ THỐNG (NO-HYPE REALITY)

Báo cáo này được lập sau khi tiến hành kiểm toán mã nguồn toàn diện và loại bỏ hoàn toàn các nhận định chủ quan, marketing hay diễn giải quá mức:

1. **Hệ thống này thuộc trường phái nào?**
   * Đây **KHÔNG PHẢI** là một cỗ máy Scalping thắng đều đặn hàng ngày/hàng tuần (High-Frequency Consistent Earner).
   * Bản chất thực sự của nó là **Mô Hình Bắt Sóng Xu Hướng Lớn (Convex Trend-Following Breakout)**:
     * Chấp nhận hòa vốn hoặc lỗ nhỏ có kiểm soát ở đa số các lệnh ($80\% - 90\%$ lệnh chạm Breakeven $+0.2\%$ hoặc dính Trailing Stop).
     * Toàn bộ lợi nhuận ròng được tạo ra bởi **các lệnh ăn trọn sóng lớn $+7\%$ đến $+10\%$ chart (tức $+70\%$ đến $+100\%$ ROE với đòn bẩy 10x)**.
2. **Hệ thống có thật sự có lãi không?**
   * **CÓ LÃI THẬT:** Trên dữ liệu sạch 5 năm với đầy đủ nến làm ấm, hệ thống mang về **$+1,599.84\text{ USDT}$ (+159.98% ROI)**.
   * **NHƯNG LÃI KHÔNG ĐỀU:** Toàn bộ lợi nhuận tập trung chủ yếu vào **giai đoạn thị trường có sóng biến động cực lớn (2025–2026)**. Trong giai đoạn thị trường đi ngang hoặc sideway (2022–2023), hệ thống chịu mức sụt giảm tài khoản **$29.73\%$** kéo dài trong 368 ngày.

---

## 2. BẢNG CHỈ SỐ ĐỊNH LƯỢNG CHUẨN XÁC

| Nhóm Chỉ Số | Tên Chỉ Số (Metric) | Giá Trị Thực Tế | Ghi Chú Đính Chính & Định Lượng |
| :--- | :--- | :---: | :--- |
| **Sinh Lời** | **Tổng Lợi Nhuận Ròng (Net Profit)** | **+1,599.84 USDT (+159.98%)** | Vốn mẫu 1,000$ tăng lên 2,599.84$ |
| | Lợi nhuận hàng năm kép (CAGR) | **21.65% / năm** | Tăng trưởng thực tế qua 4.85 năm |
| | Profit Factor (Lãi gộp / Lỗ gộp) | **1.37** | Lãi gộp 5,887.89$ / Lỗ gộp 4,288.05$ |
| | **Hệ số Payoff Thực Tế (Win/Loss Payoff)** | **1.39 lần** | **Lãi TB/lệnh thắng: 38.2$ \| Lỗ TB/lệnh thua: 27.5$** (Đính chính: Không phải 1:2.4) |
| **Rủi Ro & Sụt Giảm**| **Đáy Rút Vốn Tối Đa (Max Account Drawdown)** | **29.73% (342.90 USDT)** | Xảy ra từ 03/06/2022 đến 07/06/2023 |
| | Thời gian Drawdown dài nhất | **368 ngày 12 giờ 15 phút** | Giai đoạn thị trường gấu đi ngang sau cú sập LUNA |
| | **Tỷ Số Calmar Chuẩn Quỹ (CAGR / Max DD)** | **0.73** | **Đính chính:** Chuẩn quỹ đầu tư là ~0.73 (Công thức Freqtrade Annualized Daily là 5.80) |
| | Tỷ Số Sharpe (Daily) | **0.89** | Độ biến động của số dư ví |
| | Tỷ Số Sortino (Daily) | **0.78** | Đo lường rủi ro biến động giảm |
| **Tần Suất & Lệnh** | Tổng số lệnh thực hiện (Total Trades) | **310 lệnh** | Trung bình 0.17 lệnh/ngày (~1.2 lệnh/tuần) |
| | Tỷ lệ thắng (Win Rate) | **49.68%** (154W / 156L) | Tương đương 1 thắng / 1 thua |
| | Chuỗi lệnh thắng / thua dài nhất | **11 thắng / 9 thua** | Được bảo vệ bởi Streak Circuit Breaker |

---

## 3. PHÂN BỔ HIỆU NĂNG TỪNG NĂM (2022 – 2026)

| Năm | Tổng Số Lệnh (WR) | Nhánh SHORT Mang Lại | Nhánh LONG Mang Lại | **TỔNG LÃI NĂM** | Đánh Giá Chu Kỳ Thị Trường |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **2022** | 50 lệnh (58.0%) | **+94.03 USDT** (49 lệnh) | -21.06 USDT (1 lệnh) | **+72.97 USDT** | Nhờ bộ lọc cấm Long khi 1D dưới EMA200, nhánh Short gánh toàn bộ danh mục |
| **2023** | 32 lệnh (34.4%) | -81.35 USDT (13 lệnh) | **+16.80 USDT** (19 lệnh) | **-64.55 USDT** | **Năm duy nhất bị âm:** Thị trường sideway tích lũy sau đáy FTX |
| **2024** | 57 lệnh (43.9%) | +31.83 USDT (28 lệnh) | **+89.54 USDT** (29 lệnh) | **+121.36 USDT** | Sóng ETF bắt đầu: Nhánh Long dẫn đầu lợi nhuận |
| **2025** | 78 lệnh (53.8%) | **+290.93 USDT** (60 lệnh) | **+296.40 USDT** (18 lệnh) | **+587.33 USDT** | **Đỉnh cao Bull run:** Cả 2 nhánh cùng thắng lớn |
| **2026** | 60 lệnh (61.7%) | **+963.09 USDT** (56 lệnh) | +2.04 USDT (4 lệnh) | **+965.13 USDT** | Sóng sập mạnh: Nhánh Short kiếm gần 1,000$ |
| **TỔNG** | **310 lệnh (49.7%)** | **+1,298.53 USDT** | **+301.31 USDT** | **+1,599.84 USDT** | **4/5 năm có lãi dương** |

---

## 4. BÀI HỌC VỀ CƠ CHẾ THOÁT LỆNH & LÝ DO TẮT `use_exit_signal`

Kiểm toán đã chứng minh một phát hiện kỹ thuật vô cùng quan trọng:
* **Khi bật `use_exit_signal = True` (gọi hàm `custom_exit`):**
  * Logic cắt sớm sau 2h (`exit_trend_invalidated`) kích hoạt tới 253 lần trong các nhịp nến retest bình thường của biểu đồ 15m.
  * Hậu quả: Bốc hơi **$-2,405\text{ USDT}$** và giết chết 16/18 lệnh ăn sóng lớn, khiến tổng lãi sụt từ $+1,600\text{ USDT}$ xuống chỉ còn $+246\text{ USDT}$.
* **Quyết định kiến trúc chính thức:**
  * **Cờ `use_exit_signal` được khóa vĩnh viễn ở trạng thái `False`.**
  * Toàn bộ việc quản trị vị thế được chuyển giao hoàn toàn cho **`custom_stoploss`** và **`minimal_roi`**:
    1. Cắt lỗ ban đầu: $-2.5\%$ (BTC), $-2.8\%$ (ETH).
    2. Khóa hòa vốn $+0.2\%$ ngay khi giá chạm $+2.0\%$ chart.
    3. Trailing Stop bám sát $2.0\%$ từ đỉnh khi giá chạm $+3.5\%$ chart.
    4. Thắt chặt Stoploss sau 24h nếu lệnh chưa có lãi.
    5. Chốt lời cố định theo bảng ROI $+7\%$ đến $+10\%$ chart.
  * Cơ chế này cho phép vị thế có đủ thời gian và không gian để phát triển thành các lệnh sóng lớn (Runners).

---

## 5. KẾT LUẬN & KHUYẾN NGHỊ VẬN HÀNH THỰC TẾ

1. **Về việc triển khai vốn thật:**
   * Hệ thống **chưa thích hợp để cấp vốn lớn ngay lập tức** nếu nhà đầu tư không chuẩn bị tâm lý cho giai đoạn chịu đựng drawdown lên tới $30\%$ trong các năm sideway.
   * **Khuyến nghị:** Cần chạy thử nghiệm Paper Trading (Dry-run) ít nhất 3 đến 6 tháng trên Binance Futures để theo dõi tỷ lệ trượt giá (slippage) và khớp lệnh thực tế trước khi cấp vốn thật.
2. **Về phân bổ vốn:**
   * Nên giảm quy mô lệnh từ $15\%$ vốn ($150\text{ USDT}$ trên $1,000\text{ USDT}$) xuống còn $5\% - 7\%$ vốn ($50 - 70\text{ USDT}$ / lệnh) để đưa Max Drawdown thực tế về dưới ngưỡng an toàn $10\% - 12\%$.
