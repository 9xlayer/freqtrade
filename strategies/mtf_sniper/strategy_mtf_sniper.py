import os
import numpy as np
import pandas as pd
from pandas import DataFrame
from datetime import datetime
import logging
from pathlib import Path
from typing import Optional
from freqtrade.strategy import (
    IStrategy,
    informative,
    DecimalParameter,
    IntParameter,
    BooleanParameter,
)
import talib.abstract as ta
from strategies.mtf_sniper.market import indicators as market_indicators
try:
    from technical import qtpylib
except ImportError:
    import freqtrade.vendor.qtpylib.indicators as qtpylib
from freqtrade.persistence import Order, Trade

logger = logging.getLogger(__name__)


class StrategyMTFSniper(IStrategy):
    """
    MTF Sniper — SensorSuite live architecture.

    Entry: SensorSuite → CandidateBuilder → DirectionalTradingEngine → cooldown.
    Risk / JEV Decision Gate / exit_policy remain on confirm/exit hooks.
    """

    INTERFACE_VERSION = 3
    strategy_version = "4.1.2-scoring"

    timeframe = "15m"
    can_short: bool = True
    startup_candle_count: int = 400

    # Default fallback flags (overridden by config.json / env vars)
    enable_jev_decision: bool = True
    enable_jev_hunter: bool = True
    enable_hunter_long: bool = False
    block_macro_bull_shorts: bool = False
    enable_dynamic_sizing: bool = False
    jev_fail_policy: str = "allow"
    _hunter: Optional[object] = None
    _risk_engine: Optional[object] = None
    _decision_adapter: Optional[object] = None
    _hunter_adapter: Optional[object] = None
    _decision_ledger: Optional[object] = None
    _trade_ledger: Optional[object] = None
    _trade_ledger_recorded_ids: Optional[set] = None
    enable_telemetry: bool = True

    @staticmethod
    def apply_cooldown(signal: pd.Series, cooldown: int) -> pd.Series:
        """
        Enforce strict candle-count cooldown using positional row index (0..len-1).
        Ensures minimum `cooldown` bars between consecutive entry signals,
        regardless of DataFrame index type (RangeIndex, DatetimeIndex, etc.).
        Condition: pos - last_pos >= cooldown.
        """
        result = pd.Series(False, index=signal.index)
        if not signal.any():
            return result
        sig_array = signal.to_numpy(dtype=bool)
        res_array = np.zeros(len(sig_array), dtype=bool)
        last_pos = -cooldown - 1
        for pos, is_sig in enumerate(sig_array):
            if is_sig and (pos - last_pos >= cooldown):
                res_array[pos] = True
                last_pos = pos
        result.iloc[:] = res_array
        return result


    @staticmethod
    def _to_bool(val: object, default: bool = False) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        return str(val).strip().lower() in ("true", "1", "yes", "on")

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._load_external_config()

    def _load_external_config(self) -> None:
        """
        Load external config with safe boolean parsing, priority order:
        1. `strategy_params` in config.json
        2. Environment variables (.env / Docker Compose / Coolify)
        3. Built-in defaults
        """
        strat_cfg = self.config.get("strategy_params", {}) if hasattr(self, "config") and self.config else {}

        # 1. JEV AI Trajectory Gatekeeper
        if "enable_jev_decision" in strat_cfg:
            self.enable_jev_decision = self._to_bool(strat_cfg["enable_jev_decision"], True)
        else:
            self.enable_jev_decision = self._to_bool(os.environ.get("ENABLE_JEV_DECISION", "true"), True)

        # 2. JEV AI Momentum & Waterfall Hunter
        if "enable_jev_hunter" in strat_cfg:
            self.enable_jev_hunter = self._to_bool(strat_cfg["enable_jev_hunter"], True)
        else:
            self.enable_jev_hunter = self._to_bool(os.environ.get("ENABLE_JEV_HUNTER", "true"), True)

        if "enable_hunter_long" in strat_cfg:
            self.enable_hunter_long = self._to_bool(strat_cfg["enable_hunter_long"], False)
        else:
            self.enable_hunter_long = self._to_bool(os.environ.get("ENABLE_HUNTER_LONG", "false"), False)

        # 3. Macro Bull Short Guard
        if "block_macro_bull_shorts" in strat_cfg:
            self.block_macro_bull_shorts = self._to_bool(strat_cfg["block_macro_bull_shorts"], False)
        else:
            self.block_macro_bull_shorts = self._to_bool(os.environ.get("BLOCK_MACRO_BULL_SHORTS", "false"), False)

        # 4. Dynamic Stake Sizing
        if "enable_dynamic_sizing" in strat_cfg:
            self.enable_dynamic_sizing = self._to_bool(strat_cfg["enable_dynamic_sizing"], False)
        else:
            self.enable_dynamic_sizing = self._to_bool(os.environ.get("ENABLE_DYNAMIC_SIZING", "false"), False)

        # 5. JEV Fail Policy ('reject' / 'allow')
        if "jev_fail_policy" in strat_cfg:
            self.jev_fail_policy = str(strat_cfg["jev_fail_policy"]).lower()
        else:
            self.jev_fail_policy = os.environ.get("JEV_FAIL_POLICY", "allow").lower()

        if "enable_telemetry" in strat_cfg:
            self.enable_telemetry = self._to_bool(strat_cfg["enable_telemetry"], True)
        else:
            self.enable_telemetry = self._to_bool(os.environ.get("ENABLE_TELEMETRY", "true"), True)

    def _init_telemetry(self) -> None:
        from strategies.mtf_sniper.telemetry import DecisionLedger, TradeLedger

        user_data = Path(self.config.get("user_data_dir", "user_data"))
        ledger_dir = user_data / "telemetry"
        self._decision_ledger = DecisionLedger(
            ledger_dir / "decision_ledger.jsonl",
            enabled=bool(self.enable_telemetry),
        )
        self._trade_ledger = TradeLedger(
            ledger_dir / "trade_ledger.jsonl",
            enabled=bool(self.enable_telemetry),
        )

    def bot_start(self, **kwargs) -> None:
        self._load_external_config()
        self._init_telemetry()
        # Force risk engine rebuild so it picks up decision ledger
        self._risk_engine = None
        logger.info(
            f"StrategyMTFSniper SensorSuite live v{self.strategy_version} started. "
            f"Active Profile: JEV Decision={self.enable_jev_decision}, "
            f"JEV Hunter escalate={self.enable_jev_hunter} "
            f"(Long promote={self.enable_hunter_long}), "
            f"Block Bull Shorts={self.block_macro_bull_shorts}, "
            f"Dynamic Sizing={self.enable_dynamic_sizing}, "
            f"Telemetry={self.enable_telemetry}"
        )
        api_key = os.environ.get("JEV_API_KEY", "")
        if self.enable_jev_decision or self.enable_jev_hunter:
            if api_key:
                masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
                logger.info(f"[JEV AI] Engine initialized successfully! API Key detected ({masked}). Target URL: https://api.typesafe.ai/v1/systemone")
            else:
                logger.warning("[JEV AI] WARNING: JEV_API_KEY is not set in environment! Gatekeeper/Hunter will operate in mock/cached mode.")
        else:
            logger.info("[JEV AI] JEV AI features are DISABLED via configuration. Operating in pure quantitative technical mode.")

    def version(self) -> str | None:
        return f"v{self.strategy_version} (Unified Risk Gate & Institutional Remediation Edition)"

    def _get_risk_engine(self):
        if self._risk_engine is None:
            from strategies.mtf_sniper.risk.risk_engine import RiskEngine
            if self._decision_ledger is None and getattr(self, "enable_telemetry", True):
                self._init_telemetry()
            self._risk_engine = RiskEngine(decision_ledger=self._decision_ledger)
        return self._risk_engine

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        return self._get_risk_engine().get_leverage(max_leverage)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str,
                            **kwargs) -> float:
        return self._get_risk_engine().get_stake_amount(
            proposed_stake=proposed_stake,
            max_stake=max_stake,
            entry_tag=entry_tag,
            enable_dynamic_sizing=bool(getattr(self, "enable_dynamic_sizing", False)),
        )

    stoploss = -0.28
    use_custom_stoploss = True
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # Per-pair parameter overrides
    custom_pair_params = {
        "BTC/USDT:USDT": {
            "stoploss": -0.25,
            "short_max_dist_ema21": 1.0,
            "short_candle_body_ratio_min": 0.62,
            "long_max_dist_ema21": 3.3,
            "long_candle_body_ratio_min": 0.63,
        },
        "ETH/USDT:USDT": {
            "stoploss": -0.28,
            "short_max_dist_ema21": 1.2,
            "short_candle_body_ratio_min": 0.60,
            "long_max_dist_ema21": 3.3,
            "long_candle_body_ratio_min": 0.63,
        },
    }

    def _get_pair_params(self, pair: str) -> dict:
        for k, v in self.custom_pair_params.items():
            if k == pair or k.split(":")[0] == pair.split(":")[0]:
                return v
        return {"stoploss": self.stoploss}

    minimal_roi = {
        "0": 1.00,
        "720": 0.70,
    }

    # =========================================================================
    # 1. ENTRY PARAMETERS (HYPEROPT-READY)
    # =========================================================================
    # Short Confluence Parameters
    short_loc_tolerance_1h = DecimalParameter(0.990, 1.005, default=0.991, decimals=3, space="buy", optimize=False)
    short_min_1h_rsi_exhaustion = DecimalParameter(52.0, 70.0, default=55.1, decimals=1, space="buy", optimize=False)
    short_lookback_support = IntParameter(12, 30, default=17, space="buy", optimize=False)
    short_candle_body_ratio_min = DecimalParameter(0.45, 0.70, default=0.62, decimals=2, space="buy", optimize=False)
    short_max_dist_ema21 = DecimalParameter(0.5, 2.5, default=1.0, decimals=1, space="buy", optimize=False)
    short_min_rsi_entry = DecimalParameter(20.0, 35.0, default=27.8, decimals=1, space="buy", optimize=False)
    short_cooldown_candles = IntParameter(48, 120, default=96, space="buy", optimize=False)

    # Long Confluence Parameters
    long_loc_tolerance_1h = DecimalParameter(0.980, 1.010, default=1.008, decimals=3, space="buy", optimize=True)
    long_max_1h_rsi_exhaustion = DecimalParameter(40.0, 68.0, default=62.4, decimals=1, space="buy", optimize=True)
    long_lookback_resistance = IntParameter(8, 25, default=22, space="buy", optimize=True)
    long_candle_body_ratio_min = DecimalParameter(0.40, 0.70, default=0.63, decimals=2, space="buy", optimize=True)
    long_max_dist_ema21 = DecimalParameter(1.0, 3.5, default=3.3, decimals=1, space="buy", optimize=True)
    long_max_rsi_entry = DecimalParameter(65.0, 85.0, default=67.6, decimals=1, space="buy", optimize=True)
    long_cooldown_candles = IntParameter(16, 96, default=18, space="buy", optimize=True)
    long_overextended_dist_max = DecimalParameter(0.35, 1.00, default=0.59, decimals=2, space="buy", optimize=True)
    long_block_1d_macd = BooleanParameter(default=True, space="buy", optimize=True)

    block_4h_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    block_1d_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    min_adx_4h = DecimalParameter(12.0, 25.0, default=18.0, decimals=1, space="buy", optimize=False)

    # --- AI AGENT LENS ENHANCEMENTS ---
    block_high_vol = BooleanParameter(default=True, space="buy", optimize=False)
    high_vol_pct = DecimalParameter(85.0, 95.0, default=90.0, decimals=1, space="buy", optimize=False)
    filter_wick_rejection = BooleanParameter(default=True, space="buy", optimize=False)
    max_rejection_wick_ratio = DecimalParameter(0.20, 0.45, default=0.35, decimals=2, space="buy", optimize=False)
    min_close_pos_ratio_long = DecimalParameter(0.55, 0.75, default=0.65, decimals=2, space="buy", optimize=False)
    max_close_pos_ratio_short = DecimalParameter(0.25, 0.45, default=0.35, decimals=2, space="buy", optimize=False)
    filter_pdh_ceiling = BooleanParameter(default=True, space="buy", optimize=False)

    # =========================================================================
    # 2. DAILY MACRO FRAME (INFORMATIVE 1D)
    # =========================================================================
    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return market_indicators.populate_indicators_1d(dataframe)


    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return market_indicators.populate_indicators_4h(dataframe)


    @informative("1h")
    def populate_indicators_1h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return market_indicators.populate_indicators_1h(dataframe)


    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return market_indicators.populate_indicators_15m(
            dataframe,
            short_lookback_support=int(self.short_lookback_support.value),
            long_lookback_resistance=int(self.long_lookback_resistance.value),
        )


    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        from strategies.mtf_sniper.execution.entry_pipeline import apply_entry_pipeline
        return apply_entry_pipeline(self, dataframe, metadata)

    # =========================================================================
    # 7. EXIT TREND (POPULATE EXIT TREND)
    # =========================================================================
    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_short"] = 0
        dataframe["exit_long"] = 0
        return dataframe

    # =========================================================================
    # 8. CONFIRM TRADE ENTRY (CIRCUIT BREAKER & CORRELATION GUARDS)
    # =========================================================================
    def _jev_confirm_entry(self, pair: str, side: str, current_time: datetime) -> bool:
        """JEV Decision Gate for confirm_trade_entry (fail policy preserved)."""
        if not getattr(self, "enable_jev_decision", False):
            return True
        try:
            from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter

            if self._decision_adapter is None:
                self._decision_adapter = JevDecisionAdapter()
            decision = self._decision_adapter

            analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if analyzed_df is None or analyzed_df.empty:
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: missing analyzed dataframe."
                    )
                    return False
                return True

            df_up_to_now = analyzed_df.loc[analyzed_df["date"] <= current_time]
            if len(df_up_to_now) < 16:
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: insufficient candle history (<16 bars)."
                    )
                    return False
                return True

            payload = decision.build_payload(df_up_to_now, pair, side, lookback=32)
            verdict = decision.evaluate(payload)
            logger.info(
                f"Jev AI Verdict: {pair} {side.upper()} @ {current_time} -> {verdict.get('decision')} "
                f"(Conf: {verdict.get('confidence')}, Regime: {verdict.get('market_regime')}, "
                f"Status: {verdict.get('status')})"
            )

            v_status = verdict.get("status", decision.status.SUCCESS.value)
            if v_status != decision.status.SUCCESS.value:
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: AI status is {v_status}"
                    )
                    return False
                logger.warning(
                    f"[JEV AI] Fail-safe policy is 'allow' -> Bypassing AI gate due to status {v_status}"
                )
                return True

            if verdict.get("decision") != "CONFIRM":
                logger.info(
                    f"Jev AI Vetoed trade: {pair} {side.upper()} @ {current_time} | "
                    f"Reasons: {verdict.get('reasons')}"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"Jev AI evaluation error: {e}")
            if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                logger.warning(
                    f"[JEV AI] Fail-safe policy active -> Trade {pair} rejected due to AI evaluation failure: {e}"
                )
                return False
            logger.warning(f"[JEV AI] Fail-safe policy is 'allow' -> Bypassing AI gate on exception: {e}")
            return True

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        def _get_price(other_pair: str, ts: datetime) -> float | None:
            df_other, _ = self.dp.get_analyzed_dataframe(other_pair, self.timeframe)
            if df_other is None or df_other.empty:
                return None
            curr_row = df_other.loc[df_other["date"] <= ts]
            if curr_row.empty:
                return None
            return float(curr_row.iloc[-1]["close"])

        auth = self._get_risk_engine().authorize_entry(
            strategy_name=self.__class__.__name__,
            pair=pair,
            side=side,
            current_time=current_time,
            closed_trades=Trade.get_trades_proxy(is_open=False),
            open_trades=Trade.get_trades_proxy(is_open=True),
            get_price=_get_price,
            jev_confirm=lambda: self._jev_confirm_entry(pair, side, current_time),
            entry_tag=entry_tag,
            sensors_triggered=[entry_tag] if entry_tag else [],
            candidate_id=f"{pair}_{side}_{entry_tag or 'na'}_{int(current_time.timestamp())}",
        )
        return bool(auth.is_approved)

    # =========================================================================
    # 9. CUSTOM EXIT (TREND INVALIDATION & TIMEOUT)
    # =========================================================================
    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        from strategies.mtf_sniper.execution.exit_policy import (
            evaluate_custom_exit,
            resolve_market_row,
        )

        if not self.dp:
            return None
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        row = resolve_market_row(df, current_time)
        if row is None:
            return None
        return evaluate_custom_exit(
            trade=trade,
            current_time=current_time,
            current_profit=current_profit,
            row=row,
        )

    # =========================================================================
    # 10. DYNAMIC CUSTOM STOPLOSS
    # =========================================================================
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        return self._get_risk_engine().get_stoploss(
            trade=trade,
            current_time=current_time,
            current_profit=current_profit,
            pair_cfg=self._get_pair_params(pair),
            default_stoploss=self.stoploss,
        )

    # =========================================================================
    # 11. CONFIRM TRADE EXIT (DYNAMIC RUNNER & CONFLICT RESOLUTION)
    # =========================================================================
    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time: datetime, **kwargs) -> bool:
        """Delegate ROI/trend-runner exit gating to execution.exit_policy."""
        from strategies.mtf_sniper.execution.exit_policy import (
            confirm_exit_allowed,
            resolve_market_row,
        )

        row = None
        if self.dp and exit_reason == "roi":
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            row = resolve_market_row(df, current_time)
        return confirm_exit_allowed(
            trade=trade,
            exit_reason=exit_reason,
            rate=rate,
            row=row,
        )

    # =========================================================================
    # 12. TELEMETRY — trade ledger on closed fills
    # =========================================================================
    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs
    ) -> None:
        """
        Persist trade outcomes on exit fills.

        Note: in backtesting, order_filled runs *before* trade.close(), so
        trade.is_open is still True. Detect exits via order side instead.
        """
        exit_side = getattr(trade, "exit_side", None)
        is_exit_fill = order.ft_order_side in (exit_side, "stoploss")
        if not is_exit_fill:
            return

        # Skip partial exits while the position remains open.
        try:
            filled = float(getattr(order, "safe_amount", None) or order.filled or 0.0)
        except (TypeError, ValueError):
            filled = 0.0
        if trade.is_open and filled > 0 and abs(filled - float(trade.amount or 0.0)) > 1e-12:
            return

        if self._trade_ledger is None:
            if getattr(self, "enable_telemetry", True):
                self._init_telemetry()
            else:
                return
        if self._trade_ledger is None:
            return

        trade_id = int(getattr(trade, "id", 0) or 0)
        recorded = getattr(self, "_trade_ledger_recorded_ids", None)
        if recorded is None:
            recorded = set()
            self._trade_ledger_recorded_ids = recorded
        if trade_id in recorded:
            return

        try:
            from strategies.mtf_sniper.core.contracts import TelemetryTradeRecord

            side = "short" if trade.is_short else "long"
            open_rate = float(trade.open_rate or 0.0)
            close_rate = float(
                trade.close_rate
                or getattr(order, "average", None)
                or getattr(order, "price", None)
                or getattr(order, "ft_price", None)
                or 0.0
            )
            max_rate = float(trade.max_rate or open_rate)
            min_rate = float(trade.min_rate or open_rate)

            if open_rate > 0:
                if trade.is_short:
                    mfe = (open_rate - min_rate) / open_rate
                    mae = (open_rate - max_rate) / open_rate
                else:
                    mfe = (max_rate - open_rate) / open_rate
                    mae = (min_rate - open_rate) / open_rate
            else:
                mfe = 0.0
                mae = 0.0

            pnl_pct = trade.close_profit
            pnl_usdt = trade.close_profit_abs
            if pnl_pct is None and close_rate > 0 and hasattr(trade, "calc_profit_ratio"):
                pnl_pct = trade.calc_profit_ratio(close_rate)
            if pnl_usdt is None and close_rate > 0 and hasattr(trade, "calc_profit"):
                pnl_usdt = trade.calc_profit(close_rate)

            enter_tag = getattr(trade, "enter_tag", None) or ""
            candidate_id = f"{pair}_{side}_{enter_tag or 'na'}_{trade_id}"
            open_date = trade.open_date_utc.isoformat() if trade.open_date_utc else ""
            close_date = (
                trade.close_date_utc.isoformat()
                if getattr(trade, "close_date_utc", None)
                else current_time.isoformat()
            )

            self._trade_ledger.record(
                TelemetryTradeRecord(
                    trade_id=trade_id,
                    candidate_id=candidate_id,
                    pair=pair,
                    side=side,
                    open_date=open_date,
                    close_date=close_date,
                    open_rate=open_rate,
                    close_rate=close_rate,
                    stake_amount=float(trade.stake_amount or 0.0),
                    leverage=float(trade.leverage or 1.0),
                    pnl_usdt=float(pnl_usdt or 0.0),
                    pnl_pct=float(pnl_pct or 0.0),
                    max_favorable_excursion=float(mfe),
                    max_adverse_excursion=float(mae),
                    exit_reason=str(trade.exit_reason or ""),
                )
            )
            recorded.add(trade_id)
        except Exception as exc:
            logger.warning(f"[TradeLedger] Failed to record closed trade {pair}: {exc}")
