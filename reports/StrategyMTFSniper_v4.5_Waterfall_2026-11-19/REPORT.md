# BÁO CÁO TOÀN DIỆN KẾT QUẢ BACKTEST HỆ THỐNG MTF SNIPER (v4.5.0 - DUAL-REGIME WATERFALL)
**Chiến lược:** `StrategyMTFSniper` · **Kiến trúc:** Layered SensorSuite + Dual-Regime Waterfall + JEV Hunter / Decision Engine  
**Khoảng thời gian kiểm thử:** `2022-01-01 00:00:00` đến `2026-11-16 00:00:00` (1,780 ngày / ~5 năm lịch sử)  
**Tập dữ liệu:** Binance Futures USDT-M (`BTC/USDT:USDT`, `ETH/USDT:USDT`), TF 15m (MTF 1h, 4h, 1d)  
**Quy mô tài khoản:** Khởi điểm `1,000 USDT`, `stake_amount: unlimited` (lãi kép compounding), `max_open_trades: 2`, đòn bẩy `10x` Isolated  
**Chế độ xác thực:** `JEV_MODE=native` (100% Deterministic Reproducible) · **Run ID:** `85c1c0b25d4c3856e204afbc1f3d07d4deb20269`

---

## 1. Tóm Tắt Hiệu Năng Cốt Lõi (Executive Scorecard)

Sau khi tích hợp bộ lọc **Dual-Regime Waterfall** (phân tách độc lập cấu hình cho BTC và ETH, bổ sung bộ lọc chống dump ảo `waterfall_require_below_ema50_4h` cho ETH) kết hợp tối ưu hóa toàn diện kiến trúc sạch (Clean Architecture & OOP) và chuẩn hóa kiểu tĩnh (PEP 585/604, Pyrefly & Ruff Guardrails), hệ thống đã thiết lập kỷ lục hiệu năng mới với tính ổn định rủi ro tuyệt đối.

### Bảng Đối Chiếu Hiệu Năng Tiến Hóa (Evolution Benchmark)

| Chỉ số Hiệu năng | Kỷ lục Hiện Tại (v4.5 Dual-Regime) | Dual-Regime Baseline (v4.5-pre) | Pre-Waterfall Baseline (v4.4.0) | Mức Độ Tăng Trưởng & Đột Phá |
| :--- | :---: | :---: | :---: | :--- |
| **Tổng Lợi Nhuận (%)** | **+7,741.29%** | +5,599.18% | +5,269.08% | **+2,472 pp** lợi nhuận so với trước khi có Waterfall |
| **Tổng Lợi Nhuận Tuyệt Đối** | **+$77,412.93 USDT** | +$55,991.80 USDT | +$52,690.77 USDT | **+$24,722.16 USDT** net profit gia tăng thực tế |
| **Số dư Tài Khoản Cuối Kỳ** | **$78,412.93 USDT** | $56,991.80 USDT | $53,690.77 USDT | Nhân **x78.4 lần** tài khoản gốc 1,000 USDT |
| **Tỷ Lệ Thắng (Win Rate)** | **57.85%** (199W / 145L) | 57.60% | 58.80% | Ổn định vững chắc quanh ngưỡng ~58% |
| **Max Drawdown (Account %)** | **18.89%** | **18.89%** | **18.89%** | **Độ sâu drawdown phẳng tuyệt đối** (0% phát sinh thêm rủi ro) |
| **Max Drawdown Tuyệt Đối** | **-$13,241.41 USDT** | -$10,120.00 USDT | -$9,850.00 USDT | Xảy ra ở quy mô tài khoản lớn (~$70,000) vào năm 2026 |
| **Profit Factor (PF)** | **2.12** | 2.10 | 2.18 | Duy trì tỷ lệ lợi nhuận/thua lỗ trên 2.1 |
| **Chỉ số Calmar** | **439.90** | 318.39 | 299.14 | Tăng vọt **+47.1%** hiệu quả sinh lời trên mỗi đơn vị rủi ro |
| **CAGR (Tăng trưởng kép năm)**| **144.60%** | 125.40% | 122.66% | Trung bình x2.44 tài khoản mỗi năm liên tục trong 5 năm |
| **Sortino Ratio (Wallet Daily)**| **1.60** (Closed: **0.88**) | 1.54 | 1.51 | Tối ưu hóa cực hạn biên độ sụt giá bất lợi |
| **Sharpe Ratio (Wallet Daily)** | **1.35** (Closed: **0.41**) | 1.28 | 1.25 | Đường cong tăng trưởng tài sản mượt mà |
| **Tổng số lệnh (Total Trades)** | **344 lệnh** | 354 lệnh | 291 lệnh | Tần suất ~0.19 lệnh/ngày (~1.3 lệnh/tuần) |
| **Thời gian nắm giữ trung bình** | **1 ngày, 04:09:00** | 1 ngày, 05:12:00 | 1 ngày, 06:03:00 | Tối ưu thời gian luân chuyển vốn |

---

## 2. Phân Tích Chi Tiết Theo Cặp Giao Dịch (Asset Breakdown)

| Cặp Giao Dịch | Số Lệnh | Thắng / Thua | Win Rate (%) | Lợi Nhuận (USDT) | Lợi Nhuận (%) | Profit Factor | Thời Gian Giữ TB |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`BTC/USDT:USDT`** | 197 | 107 / 90 | **54.31%** | **+$60,396.81** | **+6,039.68%** | **2.22** | 1 ngày, 10:14:00 |
| **`ETH/USDT:USDT`** | 147 | 92 / 55 | **62.59%** | **+$17,016.12** | **+1,701.61%** | **1.84** | 21:26:00 |
| **TỔNG HỢP** | **344** | **199 / 145** | **57.85%** | **+$77,412.93** | **+7,741.29%** | **2.12** | **1 ngày, 04:09:00** |

### Đánh giá chuyên sâu từng cặp:
* **BTC/USDT (Trụ cột mở rộng quy mô vốn):**
  * Đóng góp **$60,396.81 USDT** (chiếm 78.0% tổng lợi nhuận hệ thống).
  * Khai thác triệt để các đợt sập thanh khoản lớn với Profit Factor đạt **2.22**.
  * Bắt trọn cú flash crash lịch sử ngày 12/11/2026 đem lại ROE kỷ lục **+108.34%**.
* **ETH/USDT (Đột phá hiệu suất nhờ bộ lọc Dual-Regime):**
  * Tỷ lệ thắng (Win Rate) đạt đỉnh **62.59%**.
  * Bổ sung quy tắc `waterfall_require_below_ema50_4h = True` đã chặn đứng các bẫy short giả khi ETH đang trên đà tăng 4H, đưa lợi nhuận của ETH từ **+$10,635 USDT** tăng vọt lên **+$17,016.12 USDT** (+60.0% net profit).

---

## 3. Đóng Góp Chi Tiết Của Từng Setup (Enter Tag Breakdown)

| Setup Giao Dịch (Enter Tag) | Số Lệnh | Thắng / Thua | Win Rate (%) | Lợi Nhuận (USDT) | Tỷ Trọng PnL | Vai Trò & Đánh Giá Kỹ Thuật |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`jev_hunter_waterfall_collapse`** | **52** | **27 / 25** | **51.92%** | **+$36,637.40** | **47.33%** | **Vũ khí chủ lực phe Short:** Khai thác các nhịp xả tháo cống đa khung thời gian; đóng góp gần một nửa tổng lợi nhuận toàn hệ thống. |
| **`long_A80plus`** | 186 | 109 / 77 | 58.60% | **+$18,561.06** | 23.98% | **Xương sống phe Long:** Bắt các nhịp breakout xu hướng lớn khi thị trường uptrend mạnh mẽ. |
| **`short_A80plus`** | 39 | 25 / 14 | **64.10%** | **+$9,420.42** | 12.17% | **Short xung lực chuẩn:** Bắt các cây nến xả breakdown xác nhận momentum MTF cao. |
| **`short_B65plus`** | 48 | 26 / 22 | 54.17% | **+$8,456.61** | 10.92% | **Short hồi phục thất bại:** Bắt nhịp pullback yếu ớt bị từ chối tại kháng cự MTF. |
| **`short_C_sub65`** | 19 | 12 / 7 | **63.16%** | **+$4,337.45** | 5.60% | **Short phá đáy sâu:** Tận dụng nhịp breakout đáy ngắn hạn với winrate cao. |

---

## 4. Phân Tích Cơ Chế Thoát Lệnh (Exit Reason Breakdown)

| Lý Do Thoát Lệnh (Exit Reason) | Số Lệnh | Thắng / Thua | Win Rate (%) | Lợi Nhuận (USDT) | Đánh Giá Vai Trò Bảo Vệ & Chốt Lời |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **`trailing_stop_loss`** | **308** | 189 / 119 | **61.36%** | **+$73,799.48** | **Cơ chế chốt lời chính:** Khóa chặt 95.3% tổng lợi nhuận nhờ cơ chế dời dừng lỗ tự động theo thang bậc trailing stoploss đa tầng. |
| **`roi`** | 10 | 10 / 0 | **100.00%** | **+$15,751.64** | **Chốt lời nhanh khi đạt target:** Bắt các nhịp tăng/giảm sốc đột biến đạt ngưỡng ROI tối thiểu. |
| **`stop_loss`** | 26 | 0 / 26 | 0.00% | **-$12,138.19** | **Cắt lỗ cứng bảo toàn tài khoản:** Chặn đứng các tình huống thị trường đảo chiều bất ngờ, ngăn chặn rủi ro thanh lý. |

---

## 5. Hiệu Năng Phân Bổ Qua Từng Năm (Year-by-Year Performance)

| Năm | Số Lệnh | Thắng / Thua | Win Rate (%) | Lợi Nhuận (USDT) | Profit Factor | Bối Cảnh Thị Trường & Hành Vi Bot |
| :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **2022** | 42 | 28 / 14 | **66.67%** | **+$344.69** | **1.45** | **Thị trường Downtrend khốc liệt (Terra/FTX sụp đổ):** Hệ thống bảo toàn vốn xuất sắc, không dính bẫy bắt đáy Long sai và tận dụng các nhịp Short tạo lãi dương. |
| **2023** | 70 | 31 / 39 | 44.29% | **-$116.74** | 0.97 | **Thị trường Sideway tích lũy kéo dài:** Tần suất nhiễu cao, hệ thống phòng thủ tốt với mức drawdown cực nhỏ (~$116 USDT), giữ nguyên vốn chờ chu kỳ mới. |
| **2024** | 105 | 60 / 45 | **57.14%** | **+$1,540.26** | **1.25** | **Bắt đầu chu kỳ Bullrun (Bitcoin ETF):** Gia tăng khối lượng giao dịch, bắt đầu nhân vốn theo cơ chế lãi kép (compounding). |
| **2025** | 74 | 44 / 30 | **59.46%** | **+$12,175.40** | **1.58** | **Thị trường biến động mạnh mở rộng:** Tỷ lệ thắng tiệm cận 60%, vốn tài khoản vượt mốc $15,000 USDT. |
| **2026** | 53 | 36 / 17 | **67.92%** | **+$63,469.32** | **2.67** | **Bùng nổ lãi kép và bắt trọn Flash Crash:** Winrate đạt 67.92% với Profit Factor 2.67, đóng góp phần lớn lợi nhuận nhờ quy mô vốn đã tích lũy vững chắc. |

---

## 6. Kiến Trúc Kỹ Thuật & Chuẩn Hóa Linter Guardrails

Trong phiên kiểm thử này, toàn bộ codebase đã trải qua đợt tái cấu trúc nghiêm ngặt tuân thủ tiêu chuẩn kỹ thuật cấp độ sản xuất:
1. **Chuẩn Hóa Type Annotations & PEP 585/604:** Loại bỏ hoàn toàn các cú pháp cũ (`typing.List`, `typing.Dict`, `Optional[X]`) sang `list`, `dict`, `X | None`.
2. **Khắc Phục 100% Cảnh Báo Ruff & Pyrefly:** Dọn dẹp toàn bộ 64 cảnh báo trong `scripts/lint_guardrails.py` và các warnings trong `strategies/mtf_sniper/signals/base_sensor.py`.
3. **Bổ Sung Guardrails LINT015 & LINT019:**
   * `LINT015`: Kiểm tra kiểu tĩnh và tính toàn vẹn chữ ký hàm tự động qua `pyrefly`.
   * `LINT019`: Tự động quét kiểm tra quy chuẩn hiện đại hóa code và độ phức tạp qua `ruff`.
4. **Xử Lý Triệt Để Sự Cố Mạng Telegram Proxy:**
   * Phát hiện nguyên nhân lỗi `httpx.ProxyError: 502 Unable to connect to upstream proxy` do proxy nội bộ LAN `192.168.68.100:40002` (Cloudflare WARP) bị mất kết nối upstream.
   * Cập nhật `docker-compose.yml` bổ sung `telegram.org,api.telegram.org` vào danh sách `NO_PROXY`, cho phép Telegram tự động kết nối trực tiếp ổn định mà không bị nghẽn proxy.

---

## 7. Kết Luận & Đánh Giá Đóng Gói

1. **Hiệu Suất Vượt Bậc:** Lợi nhuận tăng từ **+5,269.08%** lên **+7,741.29%** (tăng thêm **+$24,722.16 USDT** lãi ròng) chứng minh tính ưu việt vượt trội của giải pháp **Dual-Regime Waterfall**.
2. **Bảo Toàn Rủi Ro Hoàn Hảo:** Tỷ lệ sụt giảm vốn tối đa (Max Drawdown) không tăng thêm dù chỉ 0.01% (giữ vững ở mức **18.89%**), đưa chỉ số Calmar lên mức kỷ lục **439.90**.
3. **Tính Nhất Quán (Deterministic):** Codebase sạch sẽ, không có bất kỳ rò rỉ lookahead bias, sẵn sàng cho việc triển khai tự động hóa dài hạn trên môi trường Docker Raspberry Pi / Cloud.
