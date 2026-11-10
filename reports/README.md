# THƯ MỤC BÁO CÁO HIỆU NĂNG BACKTEST (REPORTS)

Thư mục này lưu trữ các báo cáo kiểm thử hiệu năng độc lập theo từng phiên bản chiến lược và mốc thời gian thực thi cụ thể. Mỗi thư mục con đại diện cho một phiên bản chiến lược và lần chạy backtest hoàn chỉnh, bao gồm tệp báo cáo markdown (`REPORT.md`), mã nguồn chiến lược tại thời điểm kiểm thử (`.py`), và tệp tham số tối ưu hóa (`.json`).

---

## 📑 Danh Mục Báo Cáo

| Thư Mục Báo Cáo | Chiến Lược | Phiên Bản / Chuyên Đề | Thời Điểm Chạy | Lợi Nhuận Ròng (PnL) | Max Drawdown | Tỷ Số Calmar |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| [`StrategyMTFSniper_StressTest_and_ChopResearch_2026-11-10`](file:///Users/nathan/Documents/WORK/freqtrade/reports/StrategyMTFSniper_StressTest_and_ChopResearch_2026-11-10/REPORT.md) | `StrategyMTFSniper` | **Stress Test & Chop Defense** | **2026-11-10 11:35:00** | **+1,424$ (ADX18) / +510$ (Stake50)** | **20.07% / 10.77%** | **1.00 (Fund)** |
| [`StrategyMTFSniper_v2.0_AuditClean_2026-11-10`](file:///Users/nathan/Documents/WORK/freqtrade/reports/StrategyMTFSniper_v2.0_AuditClean_2026-11-10/REPORT.md) | `StrategyMTFSniper` | `v2.0` (Audit Clean) | 2026-11-10 11:04:02 | +1,599.84 USDT (+159.98%) | 29.73% | 0.73 (Fund) / 5.80 (FT) |
| [`StrategyMTFSniper_v1.1_2026-11-10`](file:///Users/nathan/Documents/WORK/freqtrade/reports/StrategyMTFSniper_v1.1_2026-11-10/REPORT.md) | `StrategyMTFSniper` | `v1.1` (Tối ưu hóa Long) | 2026-11-10 10:41:02 | +1,964.26 USDT (+196.4%) | 10.72% | 19.75 |
| [`StrategyMTFSniper_v1.0_2026-11-10`](file:///Users/nathan/Documents/WORK/freqtrade/reports/StrategyMTFSniper_v1.0_2026-11-10/REPORT.md) | `StrategyMTFSniper` | `v1.0` (Bi-Directional) | 2026-11-10 10:19:46 | +1,732.20 USDT (+173.2%) | 11.61% | 16.08 |
| [`StrategyMTFShortSniper_v1.0_2026-11-10`](file:///Users/nathan/Documents/WORK/freqtrade/reports/StrategyMTFShortSniper_v1.0_2026-11-10/REPORT.md) | `StrategyMTFShortSniper` | `v1.0` (Short Baseline) | 2026-11-10 10:00:04 | +1,449.95 USDT (+145.0%) | 22.29% | 7.01 |

---

## 🗂️ Quy Chuẩn Đặt Tên Thư Mục
* Định dạng: `<TênChiếnLược>_<Version>_<YYYY-MM-DD>`
* Ví dụ: `StrategyMTFSniper_v1.1_2026-11-10`
