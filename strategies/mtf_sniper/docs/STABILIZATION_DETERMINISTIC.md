# Ổn Định Hoá — MTF Sniper Tất Định (Deterministic Stabilization)

**Nhánh:** `pi-deploy` · **Package:** `strategies/mtf_sniper` · **Milestone ổn định hoá:** `4.4.1-deterministic` (package hiện tại: `4.7.0-1w-regime`) · **Chế độ:** Hunter TẮT theo thiết kế

## Các Chế Độ Vận Hành (Runtime Modes)

| Chế độ | Hunter | Decision | AI Ngoài (External) |
|---|---|---|---|
| `pure_deterministic` (alias `pure`) | ép TẮT | **ép TẮT** | chặn |
| `deterministic` (alias `deterministic_native`) | ép TẮT | tuỳ chọn `native` | chặn |
| `decision_native` | ép TẮT | tuỳ chọn `native` | chặn |
| `decision_external` | mặc định vẫn TẮT ở pha này | tuỳ chọn | cho phép |

Cấu hình qua `strategy_params.mtf_runtime_mode` hoặc biến môi trường `MTF_RUNTIME_MODE`.

- **Production Docker (`docker/config.mtf_sniper.json` + `docker-compose.yml`):** `"mtf_runtime_mode": "decision_external"` + `"enable_jev_decision": true` + `JEV_FAIL_POLICY=fallback_native` + `enable_waterfall=true` + `enable_dynamic_sizing=true` + `"exchange_sync_interval_secs": 15`.
- **Backtest Local tất định:** `"mtf_runtime_mode": "deterministic"` + `JEV_MODE=native`.
- **Đo riêng Sniper thuần:** `"mtf_runtime_mode": "pure_deterministic"` (Decision không can thiệp vào kết quả xác nhận).

## Sơ Đồ Gọi Hàm (Deterministic Call Graph)

Hai đường **cài đặt** dùng chung một hợp đồng Policy (chi tiết trong `ARCHITECTURE.md`):

```text
populate_entry_trend
  → EntryPipeline.live (luôn ép hunter_escalation=None trên live)
      Fast:  cached Sniper Parts → vectorized AND → TradeCandidate
      Event: SensorSuite → CandidateBuilder → SetupComposer → TradeCandidate
      → Long/Short Policy (hợp lệ setup ≠ nhãn chất lượng quality_grade)
      → strategy cooldown → enter_*
      → CandidateRegistry.remember(canonical candidate_id)
confirm_trade_entry
  → truy xuất candidate_id:
       registry.lookup (tối đa 6h, không đọc nến tương lai)
       → nếu thiếu: tái tạo từ cột enter_* (cùng giới hạn 6h)
       → nếu vẫn thiếu: gắn candidate_id_missing
  → RiskEngine.authorize_entry
      → portfolio guards (circuit breaker / directional lock / distress / max open)
      → JevDecisionAdapter(force_native=True) nếu bật enable_jev_decision
      → RiskAuthorization (chỉ cổng duyệt lệnh; stake/leverage thật nằm ở callback riêng)
```

### Ý Định Config vs Thực Thi Của Hunter

`RuntimeProfile.enable_jev_hunter` có thể nhận `True` nếu config dưới `decision_external` yêu cầu.  
**Tuy nhiên đường vào lệnh live vẫn ép cứng `hunter_escalation=None`.** Nên giữ các cờ Hunter là `false` để tránh hiểu nhầm lúc khởi động cho tới khi có dự án riêng đánh giá mở lại Hunter.

## Các Lỗi Đã Khắc Phục Trong Chuỗi Ổn Định Hoá

1. **Lỗi pickle khi chạy Hyperopt đa luồng:** khoá ledger & tự tắt ghi đĩa ledger khi `runmode=hyperopt`.
2. **Nhảy nhầm quality grade thành accept:** tách bạch hoàn toàn nhãn `REJECT` với quyền hợp lệ `is_valid=True` ở setup Sniper chuẩn.
3. **Trôi lệch `candidate_id`:** dùng chung `make_candidate_id` + chuẩn hoá UTC cho cả Fast Path và Builder.
4. **Thiếu chế độ vận hành tường minh:** bổ sung `RuntimeMode` fail-fast + log fingerprint khi startup.
5. **Rò rỉ External AI khi đang chạy intent deterministic:** ép chế độ `native`, có spy-test bảo vệ.
6. **Thiếu minh bạch ở `RiskAuthorization`:** tách rõ đây là cổng kiểm duyệt, không phải nơi tính size lệnh (`sizing_method=freqtrade_proposed`).
7. **Lệch tham số config:** tạo `EffectiveConfig` + cảnh báo khi `custom_pair_params` che giá trị `Parameter` (ví dụ BTC/ETH giữ body `0.63` / dist `3.3` của Ep46).
8. **Đồng nhất ngữ nghĩa Fast vs Event Path:** chuẩn hoá thứ tự parts và confluence trong `SetupComposer`.
9. **Chế độ `pure_deterministic`:** cô lập Sniper để đo lường riêng không có Decision Gate.
10. **Tác dụng phụ sửa `os.environ`:** adapter & Strategy truyền `JEV_MODE` trực tiếp, không ghi đè biến môi trường toàn cục.
11. **Liên kết `candidate_id` xuyên suốt vòng đời lệnh:** từ `populate` -> `confirm` -> `order_filled` (lưu vào custom data của `Trade` để sống sót qua restart) -> `TradeLedger`.
12. **Không tự chế ID sai quy chuẩn:** mất liên kết thì ghi `candidate_id_missing`, không tự bịa chuỗi `{pair}_{side}_{tag}_{ts}`.
13. **Sai tên runtime mode sẽ dừng ngay (fail-fast):** ném `InvalidRuntimeModeError`, không âm thầm hạ/nâng quyền mode.
14. **Gia cố fail-closed `NaN`/`Inf` & `floor_20d_valid` (Nov 25, 2026):** chặn bypass `NaN`/`close <= 0` ở Policy/Pipeline, không gán nhầm vacuum breakdown khi thiếu `swing_low_20d_1d`, gộp trùng `accepted` trên cùng cây nến, và cứng hoá `CircuitBreaker`/`DistressGuard` trước `None`/`NaN` và lệch UTC.
15. **Giảm size `0.50x` ngược pha 1D & chuẩn hoá config + Auto-Sync sàn live (Nov 25, 2026):** thêm `counter_1d_stake_scale`, gom công tắc vào `config_resolve.py`, và làm mới vị thế live mỗi `15s` trong `bot_loop_start`.

## Ý Nghĩa: Policy vs Decision Gate

| Trường | Ý nghĩa |
|---|---|
| `PolicyEvaluationResult.is_valid` | Điều kiện **đủ chuẩn** của setup (có được đi tiếp hay không) |
| `quality_grade` | Xếp hạng chất lượng (`A`/`B`/`C`/`REJECT`) — chỉ tham khảo ở tầng Policy |
| Decision `native` | Tự tính điểm từ trajectory 32 nến + vĩ mô — **không** đọc `quality_grade` |

## Cấu Hình Hiệu Lực (`config_resolve.py`)

- Khi khởi động, Strategy in `[EffectiveConfig] {…, fingerprint}` và các cảnh báo `[ConfigResolve]` nếu override theo cặp che mất tham số Hyperopt.
- `custom_pair_params` mặc định cố tình giữ body/dist dài/ngắn cho BTC & ETH theo kết quả Ep46; có thể mở rộng qua `strategy_params.pair_overrides`.

## Rủi Ro / Giới Hạn Còn Tồn Tại

- **Chưa tính size theo khoảng cách Stoploss (Stop-distance risk budgeting):** hiện sizing mới theo tỷ lệ vốn đề xuất $\times$ tag $\times$ hệ số ngược 1D `0.50x`.
- **Kiểm tra ngưỡng thanh lý / margin trực tiếp:** mới có enum, chưa đấu nối trực tiếp với margin real-time của sàn.
- **Test restart container Docker thực tế khi đang có lệnh mở:** đã test bền vững dữ liệu qua SQLite ORM nhưng vẫn nên test thao tác restart container thật trên Pi.
- **Telegram / `stoploss_on_exchange`:** hiện đang tắt trong `user_data/config.json` và cấu hình mặc định (cắt lỗ dựa vào `custom_stoploss` phần mềm).
- **Mở rộng JEV Hunter / Momentum Radar ngoài Waterfall:** vẫn nằm ngoài phạm vi chạy live.
