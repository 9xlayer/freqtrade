# MTF Sniper — Checklist Chuyển Đổi Sang Giao Dịch Tiền Thật (Live Readiness)

**Package:** `strategies/mtf_sniper/` · **Version:** `4.7.0-1w-regime` · **Decision Engine:** `2.7.2`  
**Cặp giao dịch:** `BTC/USDT:USDT`, `ETH/USDT:USDT` · **Chế độ:** Binance USDⓈ-M Futures `Isolated 10x`

Tài liệu này là **bảng kiểm bắt buộc (Pre-Flight & Go-Live Checklist)** trước khi chuyển `"dry_run": true` sang `"dry_run": false`. Chỉ bật tiền thật khi **100% các mục P0 (Sống còn)** đã được tích `[x]`.

---

## Giai đoạn 1 — Cài đặt Tài khoản Binance Futures & API Key `[P0 - Sống còn]`

Freqtrade sẽ bị sàn từ chối lệnh ngay lập tức nếu chế độ tài khoản trên app/web Binance đặt sai:

- [ ] **Position Mode = `One-way Mode` (Chế độ Một chiều):** Vào *Binance Futures -> Preferences -> Position Mode* và xác nhận đang ở **One-way Mode** (tuyệt đối **không** bật *Hedge Mode*, nếu bật Binance sẽ trả lỗi `-4061` và từ chối cả lệnh vào lẫn lệnh cắt lỗ).
- [ ] **Asset Mode = `Single-Asset Mode` (Chế độ Đơn tài sản):** Vào *Preferences -> Asset Mode* và tắt *Multi-Assets Mode* (Binance cấm chế độ `Isolated` khi đang bật *Multi-Assets Mode*, gây lỗi `-4168`).
- [ ] **Đòn bẩy & Ký quỹ mặc định trên sàn:** Mở tab `BTCUSDT` và `ETHUSDT` Perpetual trên Binance, chuyển sẵn sang **`Isolated`** và **`10x`** để đồng bộ ngay từ lệnh đầu tiên.
- [ ] **Số dư tối thiểu ví USDⓈ-M Futures (`>= 500 USDT` khi dùng `risk_per_trade_pct = 0.025`):**
  - *Lý do toán học:* Sàn Binance yêu cầu giá trị danh nghĩa tối thiểu (`MIN_NOTIONAL`) của `BTC/USDT:USDT` là **`100 USDT`** (`~10 USDT` ký quỹ ở `10x`). Với `risk_per_trade_pct = 0.025` và `SL = -25% ROE`, nếu lệnh BTC đồng thời rơi vào nhóm hạ vốn kép (`0.50x * 0.50x = 0.25x`), ví `500 USDT` sẽ cấp `12.5 USDT` margin (`125 USDT` notional `> 100 USDT` — hợp lệ), trong khi ví `300 USDT` chỉ cấp `7.5 USDT` margin (`75 USDT < 100 USDT`) và sẽ bị `RiskEngine` chặn với mã `RISK_BELOW_MIN_STAKE`.
- [ ] **Quyền hạn API Key tối thiểu (Least Privilege):**
  - Chỉ bật **`Enable Reading`** và **`Enable Futures`**.
  - **Tuyệt đối tắt** `Enable Withdrawals` (Rút tiền), `Enable Margin`, `Enable Universal Transfer`.
  - Khuyến nghị gắn **IP Access Restriction** (lưu ý: nếu không gắn IP tĩnh, API quyền Futures của Binance sẽ tự hết hạn sau mỗi **90 ngày** — cần đặt lịch nhắc gia hạn).

---

## Giai đoạn 2 — Làm Sạch Dữ Liệu Dry-Run & Bảo Mật Hệ Thống `[P0 - Sống còn]`

> **Cảnh báo cấu hình Docker hiện tại:** Trong `docker-compose.yml`, cờ `--db-url sqlite:////freqtrade/user_data/tradesv3.sqlite` đang được truyền cứng cho cả lúc chạy `dry_run: true`. Nếu bật `dry_run: false` mà không dọn file này, Freqtrade sẽ đọc nhầm các lệnh giả lập cũ là lệnh thật (gây loạn đồng bộ vị thế sàn và sai lệch bộ đếm `CircuitBreaker`).

- [ ] **Dừng container và kiểm tra không còn lệnh treo:** Chạy `docker compose down` trên Raspberry Pi / Coolify.
- [ ] **Lưu trữ (Archive) toàn bộ DB & Ledger của đợt Dry-Run sang thư mục riêng:**
  ```bash
  mkdir -p /data/freqtrade/user_data/archive_dryrun_$(date +%Y%m%d)
  mv /data/freqtrade/user_data/tradesv3.sqlite* /data/freqtrade/user_data/archive_dryrun_$(date +%Y%m%d)/ 2>/dev/null || true
  mv /data/freqtrade/user_data/jev_hunter_cache.sqlite* /data/freqtrade/user_data/archive_dryrun_$(date +%Y%m%d)/ 2>/dev/null || true
  mv /data/freqtrade/user_data/logs/decision_ledger.jsonl* /data/freqtrade/user_data/archive_dryrun_$(date +%Y%m%d)/ 2>/dev/null || true
  mv /data/freqtrade/user_data/logs/trade_ledger.jsonl* /data/freqtrade/user_data/archive_dryrun_$(date +%Y%m%d)/ 2>/dev/null || true
  ```
- [ ] **Đổi toàn bộ mật khẩu mặc định của `api_server` trong `/data/freqtrade/user_data/config.json`:**
  - Thay mới `jwt_secret_key` và `ws_token` bằng chuỗi ngẫu nhiên dài (`openssl rand -hex 32`).
  - Thay mới `username` / `password` (hiện đang là mật khẩu mẫu `SuperSecurePassword123!`).
  - Giữ `"force_entry_enable": false` và không mở thẳng cổng `8088` ra Internet công cộng (chỉ truy cập qua LAN nội bộ, VPN/Tailscale hoặc Cloudflare Access).
- [ ] **Kiểm tra đồng bộ đồng hồ hệ thống (NTP) trên Raspberry Pi:**
  - Chạy `timedatectl status` và xác nhận `System clock synchronized: yes`. Nếu lệch giờ `> 1000ms` (hay gặp sau khi Pi mất điện), Binance sẽ từ chối mọi lệnh đặt/cắt lỗ với lỗi `-1021 (Timestamp outside of recvWindow)`.

---

## Giai đoạn 3 — Khoá Cấu Hình Khớp Lệnh & Quản Trị Vốn Thực Chiến `[P0 - Sống còn]`

> **Lưu ý thứ tự ghi đè cấu hình (`docker-compose.yml`):** Container nạp `--config /freqtrade/user_data/config.json` trước, sau đó đè `--config /freqtrade/config.mtf_sniper.json` lên trên, rồi mới đến biến môi trường nếu khóa không có trong `strategy_params`. Hãy kiểm tra đồng nhất **cả 2 file** để không bị ghi đè ngầm.

- [ ] **1. Chuyển sang chế độ Tiền thật:** Đặt `"dry_run": false` trong `/data/freqtrade/user_data/config.json` và điền `exchange.key` + `exchange.secret`.
- [ ] **2. Bật cứng lệnh cắt lỗ trực tiếp trên máy chủ Binance (`stoploss_on_exchange: true`):**
  Thêm khối `order_types` vào `/data/freqtrade/user_data/config.json` để bảo vệ vị thế `10x` ngay cả khi Raspberry Pi mất điện hoặc đứt mạng:
  ```json
  "order_types": {
    "entry": "limit",
    "exit": "limit",
    "emergency_exit": "market",
    "force_exit": "market",
    "force_entry": "market",
    "stoploss": "market",
    "stoploss_on_exchange": true,
    "stoploss_on_exchange_interval": 60,
    "stoploss_on_exchange_limit_ratio": 0.99
  }
  ```
- [ ] **3. Chống bỏ lỡ siêu sóng & chống kẹt lệnh thoát (`entry_pricing` / `exit_pricing` / `unfilledtimeout`):**
  Đổi `price_side` từ `"same"` sang `"other"` để lệnh Breakout / Waterfall khớp tức thì qua phía đối ứng của sổ lệnh thay vì treo chờ bị huỷ:
  ```json
  "unfilledtimeout": {
    "entry": 3,
    "exit": 5,
    "exit_timeout_count": 2,
    "unit": "minutes"
  },
  "entry_pricing": {
    "price_side": "other",
    "use_order_book": true,
    "order_book_top": 1
  },
  "exit_pricing": {
    "price_side": "other",
    "use_order_book": true,
    "order_book_top": 1
  }
  ```
- [ ] **4. Khoá trần rủi ro tài khoản trên mỗi lệnh (`risk_per_trade_pct`) & hạ kèo hạng B (`tag_multipliers`):**
  Cập nhật `strategy_params` trong `docker/config.mtf_sniper.json` (và đồng bộ trong `/data/freqtrade/user_data/config.json`):
  ```json
  "strategy_params": {
    "mtf_runtime_mode": "decision_native",
    "enable_jev_decision": true,
    "enable_waterfall": true,
    "block_macro_bull_shorts": false,
    "enable_dynamic_sizing": true,
    "risk_per_trade_pct": 0.025,
    "jev_1w_bear_penalty": 0.35,
    "exchange_sync_interval_secs": 15,
    "tag_multipliers": {
      "long_A80_plus": 1.0,
      "short_A80_plus": 1.0,
      "short_B65_plus": 0.5,
      "short_C_sub65": 0.5,
      "waterfall": 1.0
    }
  }
  ```
  - Đặt `"tradable_balance_ratio": 0.95` khi đã bật `"risk_per_trade_pct": 0.025` (trần rủi ro `2.5%` tổng vốn ví sẽ tự động khống chế khối lượng ký quỹ mỗi lệnh, đảm bảo dù cả BTC và ETH cùng dính SL cứng trên một cây nến thì tổng sụt giảm tối đa chỉ quanh `~4.5%–5.0%` tài khoản).
  - *Tuỳ chọn nâng cao nếu muốn giữ AI bên ngoài:* Nếu chuyển `mtf_runtime_mode` sang `"decision_external"`, bắt buộc xác nhận `JEV_FAIL_POLICY=fallback_native`, `JEV_API_TIMEOUT=4`, và kiểm tra LAN proxy `192.168.68.100:40002` không gây nghẽn mạng.

---

## Giai đoạn 4 — Cảnh Báo Thời Gian Thực & Kiểm Chứng Trước Giờ G `[P1 - Vận hành]`

- [ ] **Bật cảnh báo Telegram (`telegram.enabled: true`):** Điền `token` và `chat_id`, thử gửi `/status` và `/balance` nhận phản hồi thành công trên điện thoại.
- [ ] **Chạy cổng kiểm định tự động trước khi build Docker image:**
  ```bash
  .venv/bin/python scripts/lint_guardrails.py --verify-all
  ```
  Xác nhận kết quả **`0` vi phạm** và **100% unit tests `passed`**.
- [ ] **Kiểm tra kết nối trực tiếp từ trong container tới Binance & Telegram:**
  ```bash
  docker exec freqtrade curl -fsS https://fapi.binance.com/fapi/v1/time
  ```
- [ ] **Kiểm tra dòng log `[EffectiveConfig]` ngay khi container khởi động:**
  Mở `user_data/logs/freqtrade.log` và kiểm tra đúng các giá trị thực tế đã nạp:
  - `strategy_version = 4.7.0-1w-regime`
  - `runtime_mode` đúng chế độ chủ đích (`decision_native` hoặc `decision_external`)
  - `risk_per_trade_pct = 0.025`
  - `jev_1w_bear_penalty = 0.35`
  - `exchange_sync_interval_secs = 15.0`

---

## Giai đoạn 5 — Nghiệm Thu Lệnh Thật Đầu Tiên & Kỷ Luật Vận Hành `[P0 - Kỷ luật Sống còn]`

### A. Kiểm tra bắt buộc ngay tại Lệnh Tiền Thật số #1
Ngay khi nhận tin nhắn Telegram báo khớp lệnh đầu tiên, mở ứng dụng Binance kiểm tra bằng mắt thường 4 điểm sau:
- [ ] Vị thế trên Binance đúng là **`Isolated 10x`** và khối lượng ký quỹ đúng công thức `~ (Equity * 0.025) / |SL_pct|` (có nhân hệ số tag/counter-trend nếu có).
- [ ] Trong tab **Open Orders (Lệnh đang mở)** trên Binance **có xuất hiện thật 1 lệnh `Stop Market` (`Reduce-Only` / `Close Position`)** ở mức giá tương ứng `-2.5%` (BTC) hoặc `-2.8%` (ETH).
- [ ] Độ trượt giá khớp thực tế so với giá đóng nến 15m nằm trong ngưỡng cho phép (`<= 0.08%` giá tài sản cơ sở).
- [ ] File `user_data/logs/decision_ledger.jsonl` ghi nhận bản ghi duyệt lệnh có `sizing_method: "equity_risk_capped"` và `candidate_id` đầy đủ (không báo `candidate_id_missing`).

### B. Lộ trình Nâng vốn & Ngưỡng Cắt Khẩn Cấp (Kill-Switch)
- [ ] **Giai đoạn Pilot (`15–20` lệnh đầu / ~3–4 tuần với vốn thăm dò `500 – 1,000 USDT`):** Xác nhận tỷ lệ khớp lệnh (`fill rate >= 95%`), lệnh `Stop Market` trên sàn tự dời mượt theo thang khoá lãi (`+1%`, `+10%`, `+25% ROE`), và không phát sinh lỗi đồng bộ khi khởi động lại container.
- [ ] **Nguyên tắc Không Can Thiệp Tay:** Không tự ý chốt non bằng tay ở vùng `+15%` đến `+40% ROE` (lợi thế thống kê của MTF Sniper đến từ các lệnh đuôi phải `+80%` đến `+120%+ ROE`). Nếu buộc phải đóng khẩn cấp trên app Binance, để bot tự đồng bộ qua `exchange_sync_interval_secs = 15s`.
- [ ] **Cầu dao ngắt khẩn cấp (Portfolio Kill-Switch):**
  - Nếu tổng vốn ví thực tế sụt giảm chạm **`-25%` tính từ đỉnh vốn cao nhất (High-Water Mark)**, hoặc phát hiện lệnh trên sàn không có `Stop Market` đi kèm: chạy `/stopbuy` (hoặc dừng bot sau khi tất toán vị thế) để kiểm toán `trade_ledger.jsonl` trước khi cấp vốn tiếp; **tuyệt đối không nhồi vốn hoặc tăng `risk_per_trade_pct` để gỡ lỗ**.
