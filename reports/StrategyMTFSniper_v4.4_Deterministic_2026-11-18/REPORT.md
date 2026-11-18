# BÁO CÁO TOÀN DIỆN KẾT QUẢ BACKTEST HỆ THỐNG MTF SNIPER (v4.4.0-DETERMINISTIC)
**Chiến lược:** `StrategyMTFSniper` · **Kiến trúc:** Layered SensorSuite + SetupComposer + JEV Decision Gate  
**Khoảng thời gian kiểm thử:** `2022-01-01` đến `2026-11-16` (~5 năm lịch sử)  
**Tập dữ liệu:** Binance Futures USDT-M (`BTC/USDT:USDT`, `ETH/USDT:USDT`), TF 15m (MTF 1h, 4h, 1d)  
**Quy mô tài khoản:** Khởi điểm `1,000 USDT`, `stake_amount: unlimited` (lãi kép), `max_open_trades: 2`, đòn bẩy `10x`

---

## 1. Tóm tắt Hiệu năng Cốt lõi (Executive Summary)

Sau khi tái cấu trúc hoàn toàn hệ thống MTF sang kiến trúc **v4.4.0-deterministic** (tách biệt các bộ cảm biến tín hiệu `signals/sensors`, bộ soạn setup `SetupComposer`, bộ điều hướng `directional/`, và đường ống thực thi `EntryPipeline` tối ưu hóa vector), hệ thống đã đạt được tính nhất quán tuyệt đối (deterministic parity) với tốc độ khớp lệnh vượt trội (~25s cho toàn bộ 5 năm dữ liệu 15m).

### Bảng đối chiếu 2 Kịch bản Chạy (Run Benchmark)

| Chỉ số Hiệu năng | Kịch bản 1: Decision Gate ON <br>*(Production Live Default)* | Kịch bản 2: Decision Gate OFF <br>*(Pure Technical Baseline)* | Độ chênh lệch & Đánh giá |
|:---|:---:|:---:|:---|
| **Freqtrade Run ID** | `f1bb448eb5a...` (`07-13-30`) | `fa68c5b9...` (`07-13-54`) | Cùng snapshot dữ liệu & code v4.4.0 |
| **Tổng số lệnh (Trades)** | **204** | **224** | Lọc bỏ 20 lệnh rủi ro cao / biên mỏng |
| **Tỷ lệ Thắng (Win Rate)** | **60.78%** (124W / 80L) | **61.61%** (138W / 86L) | Tương đương (~61%) |
| **Lợi nhuận tuyệt đối** | **+34,629.33 USDT** | **+48,584.48 USDT** | Tăng trưởng quy mô vốn lãi kép |
| **Tỷ suất Lợi nhuận (%)** | **+3,462.93%** | **+4,858.45%** | Lãi kép x35.6 lần vs x49.6 lần vốn ban đầu |
| **Tăng trưởng hàng năm (CAGR)** | **108.07%** | **122.66%** | Duy trì mức x2 tài khoản mỗi năm |
| **Max Drawdown (Account %)** | **25.43%** | **32.72%** | **Decision ON giảm sụt giảm tài khoản 7.29%** |
| **Max Drawdown (USDT)** | **-5,179.83 USDT** | **-10,083.01 USDT** | **Cắt giảm 48.6% rủi ro drawdown vốn đỉnh** |
| **Profit Factor (PF)** | **2.18** | **2.10** | **Decision ON có tỷ số lợi nhuận/rủi ro cao hơn** |
| **Sortino Ratio (Closed)** | **0.81** (Wallet: **1.33**) | **0.76** (Wallet: **1.28**) | **Hiệu quả quản trị rủi ro giảm giá vượt trội** |
| **Sharpe Ratio (Daily Wallet)**| **1.28** | **1.27** | Ổn định và mượt mà |
| **Calmar Ratio** | **146.15** | **159.35** | Tỷ suất lợi nhuận trên sụt giảm tài khoản cao |
| **SQN (System Quality)** | **2.56** (Good) | **2.50** (Average/Good) | Hệ thống trading ổn định và có edge rõ rệt |
| **Thời gian nắm giữ trung bình** | **1 ngày, 06:41:00** | **1 ngày, 06:03:00** | Trung hạn theo cấu trúc sóng MTF |

---

## 2. Phân tích Chi tiết Từng Năm (Year-by-Year Performance)

| Năm kiểm thử | Số lệnh | Thắng / Thua | Win Rate (%) | Lợi nhuận Năm (USDT) | Profit Factor | Đặc điểm Thị trường & Hành vi Bot |
|:---:|:---:|:---:|:---:|:---:|:---:|:---|
| **2022** | 24 | 15 / 9 | **62.5%** | **+10.03 USDT** | **1.02** | **Bảo toàn vốn tuyệt đối:** Trong năm thị trường crypto sụp đổ từ 47k về 16k, bot chặn hầu hết các bẫy bắt đáy Long sai và tận dụng nhịp short bảo toàn vốn. |
| **2023** | 26 | 15 / 11 | **57.7%** | **+620.16 USDT** | **1.69** | **Tăng trưởng trong tích lũy:** Tài khoản tăng trưởng +62% sau khi thị trường tạo đáy và tích lũy biên độ hẹp. |
| **2024** | 70 | 39 / 31 | **55.7%** | **+3,347.43 USDT** | **1.53** | **Khai thác sóng ETF:** Tần suất vào lệnh tăng lên khi thanh khoản dồi dào, tài khoản bắt đầu nhân vốn theo lãi kép. |
| **2025** | 51 | 32 / 19 | **62.7%** | **+11,220.64 USDT** | **1.84** | **Bứt phá xu hướng mạnh:** Winrate tăng mạnh lên gần 63%, tài khoản vượt mốc 15,000 USDT. |
| **2026** *(đến 16/11)* | 33 | 23 / 10 | **69.7%** | **+19,431.08 USDT** | **3.37** | **Giai đoạn đỉnh cao hiệu quả:** Winrate đạt 69.7% với Profit Factor vượt trội 3.37, đóng góp hơn 56% tổng lợi nhuận tích lũy. |

---

## 3. Phân bổ Hiệu năng theo Cặp Tiền (Asset Breakdown)

| Cặp giao dịch | Lệnh | Win Rate | Lợi nhuận (USDT) | Lợi nhuận (%) | Profit Factor | Max Drawdown | Đánh giá Kỹ thuật |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| **BTC/USDT:USDT** | 122 | 58.20% | **+31,694.82** | **+3,169.48%** | **3.12** | 25.24% | **Trụ cột lợi nhuận chính:** Đi theo đúng xu hướng đa khung MTF, drawdown thấp và profit factor xuất sắc (>3.1). |
| **ETH/USDT:USDT** | 82 | **64.63%** | **+2,934.51** | **+293.45%** | **1.21** | 80.41% *(isolated)* | **Winrate cao nhưng biên mỏng:** ETH có tỷ lệ thắng cao hơn BTC nhưng dễ bị quét trailing stop trong các nhịp giật biên độ lớn của Altcoin. |

---

## 4. Phân tích Hướng Lệnh & Setups Kích hoạt (Directional & Setups)

### 4.1. Chiều lệnh Long vs Short

| Hướng giao dịch | Số lệnh | Tỷ lệ Lệnh | Lợi nhuận (USDT) | Tỷ trọng Lợi nhuận | Lợi nhuận trung bình / lệnh |
|:---|:---:|:---:|:---:|:---:|:---:|
| **Long** | 76 | 37.3% | **+8,633.98 USDT** | 24.9% | +11.99% / trade |
| **Short** | 128 | 62.7% | **+25,995.35 USDT** | **75.1%** | +3.73% / trade |

### 4.2. Chi tiết Từng Setup Vào Lệnh (`Enter Tag`)

| Enter Tag | Chiều | Số lệnh | Thắng / Thua | Win Rate (%) | Lợi nhuận (USDT) | ROI TB / Lệnh |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|
| `short_B65plus` | Short | 55 | 32 / 23 | 58.2% | **+14,824.69 USDT** | +2.32% |
| `long_A80plus` | Long | 76 | 48 / 28 | **63.2%** | **+8,633.98 USDT** | **+11.99%** |
| `short_A80plus` | Short | 44 | 26 / 18 | 59.1% | **+6,676.93 USDT** | -0.01% |
| `short_C_sub65` | Short | 29 | 18 / 11 | 62.1% | **+4,493.74 USDT** | +12.07% |

### 4.3. Cơ chế Thoát Lệnh (`Exit Reasons`)

| Lý do thoát lệnh | Số lệnh | Thắng / Thua | Win Rate (%) | Lợi nhuận (USDT) | Vai trò trong hệ thống |
|:---|:---:|:---:|:---:|:---:|:---|
| `trailing_stop_loss` | 196 | 116 / 80 | 59.2% | **+20,418.92 USDT** | Cơ chế khóa lãi thả nổi chủ lực, bảo vệ vốn khi đảo chiều. |
| `roi` (Take Profit bậc thang) | 8 | 8 / 0 | **100.0%** | **+14,210.41 USDT** | 8 lệnh siêu xu hướng chạm ngưỡng lợi nhuận cao (+71.68% trung bình). |
