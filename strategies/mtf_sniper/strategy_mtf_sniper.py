import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.persistence import Order, Trade
from freqtrade.strategy import (
    BooleanParameter,
    DecimalParameter,
    IntParameter,
    IStrategy,
    informative,
)
from strategies.mtf_sniper import STRATEGY_VERSION
from strategies.mtf_sniper.market import indicators as market_indicators


logger = logging.getLogger(__name__)


class StrategyMTFSniper(IStrategy):
    """
    MTF Sniper — deterministic layered entry (v4.7.0-1w-regime).

    Entry: part sensors + MarketRegimeSensor (1W) → SetupComposer → Policy → cooldown.
    Confirm: optional JEV Decision Gate + Risk.
    """

    INTERFACE_VERSION = 3
    strategy_version = STRATEGY_VERSION

    timeframe = "15m"
    can_short: bool = True
    startup_candle_count: int = 400

    enable_jev_decision: bool = True
    enable_waterfall: bool = True
    block_macro_bull_shorts: bool = False
    enable_dynamic_sizing: bool = False
    default_leverage: float = 10.0
    _tag_multipliers: dict[str, float] | None = None
    _counter_1d_scale_override: float | None = None
    counter_1w_stake_scale: float | None = None
    jev_1w_bear_penalty: float = 0.0
    exchange_sync_interval_secs: float = 15.0
    _last_exchange_sync_ts: float | None = None
    jev_fail_policy: str = "allow"
    mtf_runtime_mode: str = "deterministic"
    _runtime_profile: Any = None
    _effective_config: Any = None
    _config_fingerprint: str = ""
    _risk_engine: Any = None
    _decision_adapter: Any = None
    _decision_ledger: Any = None
    _trade_ledger: Any = None
    _trade_ledger_recorded_ids: set | None = None
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
        from strategies.mtf_sniper.core.config_resolve import parse_bool

        return parse_bool(val, default)

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self._load_external_config()

    def _load_external_config(self) -> None:
        """
        Load external config with safe parsing, priority order:
        1. Runtime mode profile (authoritative for AI/Decision mode)
        2. `strategy_params` in config.json
        3. Environment variables (.env / Docker Compose / Coolify)
        4. Built-in defaults & Freqtrade hyperopt Parameter defaults
        """
        from strategies.mtf_sniper.core.config_resolve import (
            build_effective_config,
            merge_pair_overrides,
            resolve_strategy_switches,
            warn_pair_override_divergences,
        )
        from strategies.mtf_sniper.core.runtime_mode import (
            InvalidRuntimeModeError,
            build_runtime_profile,
        )

        strat_cfg = (
            self.config.get("strategy_params", {})
            if hasattr(self, "config") and self.config
            else {}
        )
        want_decision = (
            self._to_bool(strat_cfg["enable_jev_decision"], True)
            if "enable_jev_decision" in strat_cfg
            else self._to_bool(os.environ.get("ENABLE_JEV_DECISION", "true"), True)
        )

        try:
            profile = build_runtime_profile(
                strat_cfg,
                enable_jev_decision_cfg=want_decision,
            )
        except InvalidRuntimeModeError:
            # Fail fast at strategy init / bot_start — do not silently fall back.
            logger.error(
                "[RuntimeMode] Invalid mtf_runtime_mode — refusing to start "
                "(no silent fallback to a more privileged mode)."
            )
            raise
        self._runtime_profile = profile
        self.mtf_runtime_mode = profile.mode.value
        self.enable_jev_decision = profile.enable_jev_decision

        switches = resolve_strategy_switches(strat_cfg)
        self.block_macro_bull_shorts = switches.block_macro_bull_shorts
        self.enable_waterfall = switches.enable_waterfall
        self.enable_dynamic_sizing = switches.enable_dynamic_sizing
        self.jev_fail_policy = switches.jev_fail_policy
        self.enable_telemetry = switches.enable_telemetry
        self.default_leverage = switches.default_leverage
        self._tag_multipliers = switches.tag_multipliers
        self.risk_per_trade_pct = switches.risk_per_trade_pct
        self.counter_1w_stake_scale = switches.counter_1w_stake_scale
        self.jev_1w_bear_penalty = switches.jev_1w_bear_penalty
        self.exchange_sync_interval_secs = switches.exchange_sync_interval_secs
        self.custom_pair_params = merge_pair_overrides(
            type(self).custom_pair_params, strat_cfg
        )
        self._counter_1d_scale_override = None
        if switches.counter_1d_stake_scale is not None and not self._is_hyperopt_run():
            param = getattr(self, "counter_1d_stake_scale", None)
            if param is not None and hasattr(param, "value"):
                param.value = switches.counter_1d_stake_scale
            else:
                self._counter_1d_scale_override = switches.counter_1d_stake_scale

        self._effective_config = build_effective_config(self, profile=profile)
        self._config_fingerprint = self._effective_config.fingerprint()
        for warn in warn_pair_override_divergences(self._effective_config):
            logger.warning(f"[ConfigResolve] {warn}")

    def _is_hyperopt_run(self) -> bool:
        runmode = self.config.get("runmode") if hasattr(self, "config") and self.config else None
        value = getattr(runmode, "value", runmode)
        return str(value).lower() == "hyperopt"

    def _init_telemetry(self) -> None:
        from strategies.mtf_sniper.telemetry import DecisionLedger, TradeLedger

        # Hyperopt forks workers via pickle — skip disk ledgers (locks + I/O overhead).
        if self._is_hyperopt_run() or not self.enable_telemetry:
            self._decision_ledger = DecisionLedger(enabled=False)
            self._trade_ledger = TradeLedger(enabled=False)
            return

        user_data = Path(self.config.get("user_data_dir", "user_data"))
        ledger_dir = user_data / "telemetry"
        self._decision_ledger = DecisionLedger(
            ledger_dir / "decision_ledger.jsonl",
            enabled=True,
        )
        self._trade_ledger = TradeLedger(
            ledger_dir / "trade_ledger.jsonl",
            enabled=True,
        )

    def bot_start(self, **kwargs) -> None:
        self._load_external_config()
        self._init_telemetry()
        # Force risk / decision rebuild with effective fingerprint
        self._risk_engine = None
        self._decision_adapter = None
        profile = self._runtime_profile
        eff = self._effective_config
        logger.info(
            f"StrategyMTFSniper live v{self.strategy_version} started. "
            f"runtime_mode={self.mtf_runtime_mode} "
            f"fingerprint={self._config_fingerprint} "
            f"Decision={self.enable_jev_decision} "
            f"(jev_mode={getattr(profile, 'decision_jev_mode', 'native')}, "
            f"external_ai={getattr(profile, 'allow_external_ai', False)}) "
            f"Waterfall={self.enable_waterfall} "
            f"block_macro_bull_shorts={self.block_macro_bull_shorts} "
            f"dynamic_sizing={self.enable_dynamic_sizing} "
            f"exchange_sync_s={self.exchange_sync_interval_secs} "
            f"telemetry={self.enable_telemetry}"
        )
        if eff is not None:
            logger.info(f"[EffectiveConfig] {eff.to_log_dict()}")
        api_key = os.environ.get("JEV_API_KEY", "")
        if getattr(profile, "allow_external_ai", False) and self.enable_jev_decision:
            if api_key:
                masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
                logger.info(
                    f"[JEV] API key detected ({masked}). "
                    f"URL: {os.environ.get('JEV_API_URL', 'https://api.typesafe.ai/v1/systemone')}"
                )
            else:
                logger.warning("[JEV] JEV_API_KEY unset — external Decision unavailable.")
        elif self.enable_jev_decision:
            logger.info("[JEV] Deterministic/native Decision Gate only — no external AI inference.")
        else:
            logger.info("[JEV] Decision disabled — pure technical path.")

    def _is_live_trading(self) -> bool:
        from collections.abc import Mapping

        cfg = getattr(self, "config", None)
        if not isinstance(cfg, Mapping) or bool(cfg.get("dry_run", True)):
            return False
        runmode = cfg.get("runmode")
        value = getattr(runmode, "value", runmode)
        return str(value).strip().lower() == "live"

    def _sync_live_exchange_positions(self, current_time: datetime) -> bool:
        """Refresh live wallet/position cache on an interval when filled trades are open.

        Freqtrade limits periodic wallet refreshes to once per hour; refreshing here before
        ``manage_open_orders`` and ``exit_positions`` lets manual exchange closes trigger
        ``handle_onexchange_order`` within seconds rather than staying stale for up to 1h.
        """
        interval = getattr(self, "exchange_sync_interval_secs", 15.0)
        if interval <= 0.0 or not self._is_live_trading():
            return False
        wallets = getattr(self, "wallets", None)
        if wallets is None or not hasattr(wallets, "update"):
            return False

        now_ts = current_time.timestamp()
        last_ts = getattr(self, "_last_exchange_sync_ts", None)
        if last_ts is not None and (now_ts - last_ts) < interval:
            return False

        open_trades = Trade.get_trades_proxy(is_open=True)
        has_filled_position = any(
            not getattr(t, "has_open_orders", False)
            and not getattr(t, "has_open_sl_orders", False)
            and getattr(t, "fee_open_currency", None) is not None
            for t in open_trades
        )
        if not has_filled_position:
            return False

        self._last_exchange_sync_ts = now_ts
        try:
            wallets.update(require_update=True)
            return True
        except Exception as exc:
            logger.warning(f"[ExchangeSync] Failed to refresh live wallets/positions: {exc}")
            return False

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        self._sync_live_exchange_positions(current_time)

    def version(self) -> str | None:
        return (
            f"v{self.strategy_version} "
            f"(mode={self.mtf_runtime_mode}; parts → SetupComposer → Policy; Decision on confirm)"
        )

    def _get_risk_engine(self):
        if self._risk_engine is None:
            from strategies.mtf_sniper.risk.risk_engine import RiskEngine

            if self._decision_ledger is None and getattr(self, "enable_telemetry", True):
                self._init_telemetry()
            max_open = 2
            if hasattr(self, "config") and self.config:
                max_open = int(self.config.get("max_open_trades", 2) or 2)
            self._risk_engine = RiskEngine(
                decision_ledger=self._decision_ledger,
                max_open_trades=max_open,
                config_fingerprint=self._config_fingerprint,
                default_leverage=float(getattr(self, "default_leverage", 10.0) or 10.0),
                tag_multipliers=getattr(self, "_tag_multipliers", None),
                risk_per_trade_pct=getattr(self, "risk_per_trade_pct", None),
            )
        return self._risk_engine

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return self._get_risk_engine().get_leverage(max_leverage)

    def _resolve_counter_1d_stake_scale(self) -> float:
        from strategies.mtf_sniper.risk.position_sizing import (
            COUNTER_1D_TREND_STAKE_SCALE,
            normalize_counter_1d_scale,
        )

        override = getattr(self, "_counter_1d_scale_override", None)
        if override is not None:
            return normalize_counter_1d_scale(override, default=COUNTER_1D_TREND_STAKE_SCALE)
        raw = getattr(self, "counter_1d_stake_scale", COUNTER_1D_TREND_STAKE_SCALE)
        val = getattr(raw, "value", raw)
        return normalize_counter_1d_scale(val, default=COUNTER_1D_TREND_STAKE_SCALE)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        enable_dynamic = bool(getattr(self, "enable_dynamic_sizing", False))
        counter_scale = 1.0
        if enable_dynamic and getattr(self, "dp", None):
            from strategies.mtf_sniper.execution.exit_policy import resolve_market_row
            from strategies.mtf_sniper.risk.position_sizing import (
                resolve_counter_1d_scale,
                resolve_counter_1w_scale,
            )

            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            row = resolve_market_row(df, current_time)
            scale_1d = resolve_counter_1d_scale(
                side, row, counter_scale=self._resolve_counter_1d_stake_scale()
            )
            scale_1w = resolve_counter_1w_scale(
                side, row, counter_1w_scale=getattr(self, "counter_1w_stake_scale", None)
            )
            counter_scale = scale_1d * scale_1w
        pair_sl = float(self._get_pair_params(pair).get("stoploss", self.stoploss) or self.stoploss)
        risk_pct = getattr(self, "risk_per_trade_pct", None)
        stake = self._get_risk_engine().get_stake_amount(
            proposed_stake=proposed_stake,
            max_stake=max_stake,
            entry_tag=entry_tag,
            enable_dynamic_sizing=enable_dynamic,
            counter_trend_scale=counter_scale,
            wallet_equity=self._resolve_wallet_equity(),
            stoploss_pct=pair_sl,
        )
        if min_stake is not None and stake < min_stake:
            if risk_pct is not None:
                logger.info(
                    f"[RiskBudget] {pair}: capped stake {stake:.2f} < min_stake {min_stake:.2f} "
                    "— rejecting entry instead of breaching risk budget."
                )
                return 0.0
            if min_stake <= max_stake:
                return min_stake
        return stake

    stoploss = -0.28
    use_custom_stoploss = True
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    process_only_new_candles = True

    # Per-pair overrides. long dist/body here mask strategy Parameter values in
    # setups.py — HO Ep46 kept these at 3.3/0.63 (optimizer samples were ignored).
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
            "waterfall_require_below_ema50_4h": True,
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

    short_loc_tolerance_1h = DecimalParameter(
        0.990, 1.005, default=0.991, decimals=3, space="buy", optimize=False
    )
    short_min_1h_rsi_exhaustion = DecimalParameter(
        52.0, 70.0, default=55.1, decimals=1, space="buy", optimize=False
    )
    short_lookback_support = IntParameter(12, 30, default=17, space="buy", optimize=False)
    short_candle_body_ratio_min = DecimalParameter(
        0.45, 0.70, default=0.62, decimals=2, space="buy", optimize=False
    )
    short_max_dist_ema21 = DecimalParameter(
        0.5, 2.5, default=1.0, decimals=1, space="buy", optimize=False
    )
    short_min_rsi_entry = DecimalParameter(
        20.0, 35.0, default=27.8, decimals=1, space="buy", optimize=False
    )
    short_cooldown_candles = IntParameter(48, 120, default=96, space="buy", optimize=False)

    long_loc_tolerance_1h = DecimalParameter(
        0.980, 1.010, default=0.988, decimals=3, space="buy", optimize=True
    )
    long_max_1h_rsi_exhaustion = DecimalParameter(
        40.0, 68.0, default=57.1, decimals=1, space="buy", optimize=True
    )
    long_lookback_resistance = IntParameter(8, 25, default=15, space="buy", optimize=True)
    long_candle_body_ratio_min = DecimalParameter(
        0.40, 0.70, default=0.63, decimals=2, space="buy", optimize=True
    )
    long_max_dist_ema21 = DecimalParameter(
        1.0, 3.5, default=3.3, decimals=1, space="buy", optimize=True
    )
    long_max_rsi_entry = DecimalParameter(
        65.0, 85.0, default=68.1, decimals=1, space="buy", optimize=True
    )
    long_cooldown_candles = IntParameter(16, 96, default=40, space="buy", optimize=True)
    long_overextended_dist_max = DecimalParameter(
        0.35, 1.00, default=0.53, decimals=2, space="buy", optimize=True
    )
    long_block_1d_macd = BooleanParameter(default=False, space="buy", optimize=True)
    counter_1d_stake_scale = DecimalParameter(
        0.25, 1.00, default=0.50, decimals=2, space="buy", optimize=True
    )

    block_4h_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    block_1d_counter_wave = BooleanParameter(default=True, space="buy", optimize=False)
    min_adx_4h = DecimalParameter(12.0, 25.0, default=18.0, decimals=1, space="buy", optimize=False)

    block_high_vol = BooleanParameter(default=True, space="buy", optimize=False)
    high_vol_pct = DecimalParameter(
        85.0, 95.0, default=90.0, decimals=1, space="buy", optimize=False
    )
    filter_wick_rejection = BooleanParameter(default=True, space="buy", optimize=False)
    max_rejection_wick_ratio = DecimalParameter(
        0.20, 0.45, default=0.35, decimals=2, space="buy", optimize=False
    )
    min_close_pos_ratio_long = DecimalParameter(
        0.55, 0.75, default=0.65, decimals=2, space="buy", optimize=False
    )
    max_close_pos_ratio_short = DecimalParameter(
        0.25, 0.45, default=0.35, decimals=2, space="buy", optimize=False
    )
    filter_pdh_ceiling = BooleanParameter(default=True, space="buy", optimize=False)

    @informative("1w")
    def populate_indicators_1w(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return market_indicators.populate_indicators_1w(dataframe)

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
            short_lookback_support=self.short_lookback_support.value,
            long_lookback_resistance=self.long_lookback_resistance.value,
        )

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        from strategies.mtf_sniper.execution.entry_pipeline import apply_entry_pipeline

        return apply_entry_pipeline(self, dataframe, metadata)

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_short"] = 0
        dataframe["exit_long"] = 0
        return dataframe

    def _is_fail_closed(self) -> bool:
        return getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe")

    def _get_valid_decision_window(
        self, pair: str, current_time: datetime, lookback: int = 32, min_bars: int = 16
    ) -> DataFrame | None:
        if not self.dp:
            return None
        analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if analyzed_df is None or analyzed_df.empty:
            return None
        up_to = (analyzed_df["date"] <= current_time).to_numpy().nonzero()[0]
        if len(up_to) == 0:
            return None
        end_pos = int(up_to[-1])
        start_pos = max(0, end_pos - lookback + 1)
        df_window = analyzed_df.iloc[start_pos : end_pos + 1]
        return df_window if len(df_window) >= min_bars else None

    def _get_decision_adapter(self):
        fail_policy = str(getattr(self, "jev_fail_policy", "allow") or "allow").lower()
        if self._decision_adapter is None:
            from strategies.mtf_sniper.intelligence.decision_adapter import JevDecisionAdapter

            profile = self._runtime_profile
            force_native = not bool(getattr(profile, "allow_external_ai", False))
            mode = getattr(profile, "decision_jev_mode", "native") if profile else "native"
            self._decision_adapter = JevDecisionAdapter(
                force_native=force_native,
                mode=mode,
                fail_policy=fail_policy,
                regime_1w_bear_penalty=getattr(self, "jev_1w_bear_penalty", 0.0),
            )
        elif getattr(self._decision_adapter, "fail_policy", None) != fail_policy:
            self._decision_adapter.fail_policy = fail_policy
        return self._decision_adapter

    def _jev_confirm_entry_detail(
        self, pair: str, side: str, current_time: datetime, entry_tag: str | None = None
    ) -> dict[str, Any]:
        """JEV Decision Gate evaluation returning decision boolean plus telemetry fields."""
        if not getattr(self, "enable_jev_decision", False):
            return {
                "approved": True,
                "ai_verdict": "DISABLED",
                "ai_confidence": 0.0,
                "ai_model_version": None,
                "market_regime": "",
            }
        try:
            decision = self._get_decision_adapter()
            lookback = 32
            df_window = self._get_valid_decision_window(
                pair, current_time, lookback=lookback, min_bars=16
            )
            if df_window is None:
                if self._is_fail_closed():
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: "
                        "insufficient candle history (<16 bars)."
                    )
                    return {
                        "approved": False,
                        "ai_verdict": "VETO",
                        "ai_confidence": 0.0,
                        "ai_model_version": None,
                        "market_regime": "INSUFFICIENT_DATA",
                    }
                return {
                    "approved": True,
                    "ai_verdict": "BYPASS",
                    "ai_confidence": 0.0,
                    "ai_model_version": None,
                    "market_regime": "INSUFFICIENT_DATA",
                }

            payload = decision.build_payload(
                df_window, pair, side, lookback=lookback, setup=entry_tag
            )
            verdict = decision.evaluate(payload)
            logger.info(
                f"Jev AI Verdict: {pair} {side.upper()} @ {current_time} -> "
                f"{verdict.get('decision')} (Conf: {verdict.get('confidence')})"
            )

            v_status = verdict.get("status", decision.status.SUCCESS.value)
            v_conf = float(verdict.get("confidence") or 0.0)
            v_model = verdict.get("model_version")
            v_regime = str(verdict.get("market_regime") or "")

            if v_status != decision.status.SUCCESS.value:
                if self._is_fail_closed():
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: status={v_status}"
                    )
                    return {
                        "approved": False,
                        "ai_verdict": "VETO",
                        "ai_confidence": v_conf,
                        "ai_model_version": v_model,
                        "market_regime": v_regime,
                    }
                logger.warning(
                    f"[JEV AI] Fail-safe policy is 'allow' -> Bypass AI gate: status {v_status}"
                )
                return {
                    "approved": True,
                    "ai_verdict": "BYPASS",
                    "ai_confidence": v_conf,
                    "ai_model_version": v_model,
                    "market_regime": v_regime,
                }

            if verdict.get("decision") != "CONFIRM":
                logger.info(
                    f"Jev AI Vetoed trade: {pair} {side.upper()} @ {current_time} | "
                    f"Reasons: {verdict.get('reasons')}"
                )
                return {
                    "approved": False,
                    "ai_verdict": str(verdict.get("decision") or "VETO"),
                    "ai_confidence": v_conf,
                    "ai_model_version": v_model,
                    "market_regime": v_regime,
                }
            return {
                "approved": True,
                "ai_verdict": "CONFIRM",
                "ai_confidence": v_conf,
                "ai_model_version": v_model,
                "market_regime": v_regime,
            }
        except Exception as e:
            logger.warning(f"Jev AI evaluation error: {e}")
            if self._is_fail_closed():
                logger.warning(
                    f"[JEV AI] Fail-safe active -> Trade {pair} rejected on AI error: {e}"
                )
                return {
                    "approved": False,
                    "ai_verdict": "VETO",
                    "ai_confidence": 0.0,
                    "ai_model_version": None,
                    "market_regime": "",
                }
            logger.warning(f"[JEV AI] Fail-safe 'allow' -> Bypassing AI gate on exception: {e}")
            return {
                "approved": True,
                "ai_verdict": "BYPASS",
                "ai_confidence": 0.0,
                "ai_model_version": None,
                "market_regime": "",
            }

    def _jev_confirm_entry(
        self, pair: str, side: str, current_time: datetime, entry_tag: str | None = None
    ) -> bool:
        """JEV Decision Gate for confirm_trade_entry (fail policy preserved)."""
        return bool(self._jev_confirm_entry_detail(pair, side, current_time, entry_tag)["approved"])

    def _resolve_candidate_id(
        self,
        pair: str,
        side: str,
        entry_tag: str | None,
        current_time: datetime,
    ) -> str | None:
        """Registry → reconstruct from analyzed enter bar. Never invent non-canonical IDs."""
        from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
        from strategies.mtf_sniper.core.candidate_registry import DEFAULT_LOOKUP_MAX_AGE
        from strategies.mtf_sniper.core.enums import TradeSide

        reg = getattr(self, "_candidate_registry", None)
        if reg is not None:
            found = reg.lookup(pair=pair, side=side, entry_tag=entry_tag, current_time=current_time)
            if found:
                return found

        # Reconstruct canonical ID from last matching enter_* bar (same max-age as registry).
        # Absolute row positions come from the boolean mask — never index-label→position
        # (duplicate labels would otherwise pick the wrong candle_index).
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            df = None
        if df is not None and not df.empty and "date" in df.columns:
            side_l = side.lower()
            col = "enter_short" if side_l == "short" else "enter_long"
            if col in df.columns:
                ct = normalize_utc(current_time)
                mask = (df["date"] <= current_time) & (df[col].fillna(0).astype(bool))
                if entry_tag and "enter_tag" in df.columns:
                    mask = mask & (df["enter_tag"].astype(str) == entry_tag)
                positions = np.flatnonzero(mask.fillna(False).to_numpy(dtype=bool))
                if len(positions) == 0:
                    return None
                candle_index = int(positions[-1])
                row = df.iloc[candle_index]
                ts = normalize_utc(row["date"])
                if ts > ct:
                    return None  # never reconstruct from a future bar
                if (ct - ts) > DEFAULT_LOOKUP_MAX_AGE:
                    logger.warning(
                        f"[CandidateID] DataFrame fallback stale for {pair} {side_l}: "
                        f"signal_age={(ct - ts)} > max={DEFAULT_LOOKUP_MAX_AGE}"
                    )
                    return None
                trade_side = TradeSide.SHORT if side_l == "short" else TradeSide.LONG
                return make_candidate_id(pair, trade_side, ts, candle_index)

        return None

    def _persist_candidate_id(self, trade: Trade, candidate_id: str | None) -> bool:
        """Write + re-read verify. Returns True only when restart-safe persist succeeded."""
        from strategies.mtf_sniper.core.candidate_id import (
            CANDIDATE_ID_CUSTOM_DATA_KEY,
            CANDIDATE_ID_MISSING,
            candidate_id_matches_trade,
            is_canonical_candidate_id,
        )

        if not candidate_id or candidate_id == CANDIDATE_ID_MISSING:
            return False
        if not is_canonical_candidate_id(candidate_id):
            logger.warning(f"[CandidateID] Refusing to persist non-canonical id={candidate_id!r}")
            return False
        trade_pair = getattr(trade, "pair", None)
        trade_side = "short" if getattr(trade, "is_short", False) else "long"
        if trade_pair and not candidate_id_matches_trade(
            candidate_id, pair=trade_pair, side=trade_side
        ):
            logger.warning(
                f"[CandidateID] Refusing to persist mismatched id={candidate_id!r} "
                f"for trade pair={trade_pair} side={trade_side}"
            )
            return False
        try:
            trade.set_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY, candidate_id)
        except Exception as exc:
            logger.warning(f"[CandidateID] candidate_id_persistence_failed set_custom_data: {exc}")
            return False
        # Verify round-trip so we never claim restart-safe without durable store.
        verified = self._read_persisted_candidate_id(trade)
        if verified != candidate_id:
            logger.warning(
                f"[CandidateID] candidate_id_persistence_failed verify "
                f"wrote={candidate_id!r} read={verified!r}"
            )
            return False
        return True

    def _read_persisted_candidate_id(self, trade: Trade) -> str | None:
        from strategies.mtf_sniper.core.candidate_id import (
            CANDIDATE_ID_CUSTOM_DATA_KEY,
            candidate_id_matches_trade,
            is_canonical_candidate_id,
        )

        try:
            val = trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY)
        except Exception:
            return None
        if val is None or val == "":
            return None
        cid = str(val)
        if not is_canonical_candidate_id(cid):
            logger.warning(f"[CandidateID] Ignoring non-canonical persisted id={cid!r}")
            return None
        trade_pair = getattr(trade, "pair", None)
        trade_side = "short" if getattr(trade, "is_short", False) else "long"
        if trade_pair and not candidate_id_matches_trade(cid, pair=trade_pair, side=trade_side):
            logger.warning(
                f"[CandidateID] Ignoring mismatched persisted id={cid!r} "
                f"for trade pair={trade_pair} side={trade_side}"
            )
            return None
        return cid

    def _lookup_accepted_candidate_meta(
        self, pair: str, candidate_id: str | None
    ) -> dict[str, Any]:
        if not candidate_id or not getattr(self, "dp", None):
            return {}
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            return {}
        if df is None:
            return {}
        for item in getattr(df, "attrs", {}).get("mtf_sniper_accepted_candidates") or []:
            if item.get("candidate_id") == candidate_id:
                return item
        return {}

    def _resolve_wallet_equity(self) -> float | None:
        wallets = getattr(self, "wallets", None)
        if wallets is None or not hasattr(wallets, "get_total"):
            return None
        cfg = getattr(self, "config", None) or {}
        stake_ccy = cfg.get("stake_currency", "USDT")
        try:
            total = float(wallets.get_total(stake_ccy))
            return total if total > 0 else None
        except (TypeError, ValueError):
            return None

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

        def _get_price(other_pair: str, ts: datetime) -> float | None:
            df_other, _ = self.dp.get_analyzed_dataframe(other_pair, self.timeframe)
            if df_other is None or df_other.empty:
                return None
            curr_row = df_other.loc[df_other["date"] <= ts]
            if curr_row.empty:
                return None
            return float(curr_row.iloc[-1]["close"])

        candidate_id = self._resolve_candidate_id(pair, side, entry_tag, current_time)
        if not candidate_id:
            logger.warning(
                f"[CandidateID] Missing at confirm {pair} {side} tag={entry_tag} "
                f"@ {current_time} — logging as {CANDIDATE_ID_MISSING}"
            )
        cand_meta = self._lookup_accepted_candidate_meta(pair, candidate_id)
        pair_cfg = self._get_pair_params(pair)
        risk_engine = self._get_risk_engine()
        lev = risk_engine.get_leverage(None) if hasattr(risk_engine, "get_leverage") else 10.0
        est_stake = (amount * rate / lev) if (amount > 0 and rate > 0 and lev > 0) else 0.0
        jev_fn = (
            self._jev_confirm_entry
            if "_jev_confirm_entry" in getattr(self, "__dict__", {})
            else self._jev_confirm_entry_detail
        )
        auth = risk_engine.authorize_entry(
            strategy_name=self.__class__.__name__,
            pair=pair,
            side=side,
            current_time=current_time,
            closed_trades=Trade.get_trades_proxy(is_open=False),
            open_trades=Trade.get_trades_proxy(is_open=True),
            get_price=_get_price,
            jev_confirm=lambda: jev_fn(pair, side, current_time, entry_tag=entry_tag),
            entry_tag=entry_tag,
            sensors_triggered=[entry_tag] if entry_tag else [],
            policy_score=float(cand_meta.get("policy_score") or 0.0),
            quality_grade=str(cand_meta.get("quality_grade") or ""),
            candidate_id=candidate_id or CANDIDATE_ID_MISSING,
            setup_type=entry_tag or "",
            signal_source=str(cand_meta.get("signal_source") or "confirm_trade_entry"),
            proposed_stake=est_stake,
            max_stake=est_stake,
            enable_dynamic_sizing=False,
            max_leverage=lev,
            default_stoploss=float(pair_cfg.get("stoploss", self.stoploss)),
            wallet_equity=self._resolve_wallet_equity(),
        )
        return auth.is_approved

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        """Custom exit hook. Exits are strictly governed by custom_stoploss ladder & minimal_roi."""
        return

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool = False,
        **kwargs,
    ) -> float | None:
        return self._get_risk_engine().get_stoploss(
            trade=trade,
            current_time=current_time,
            current_profit=current_profit,
            pair_cfg=self._get_pair_params(pair),
            default_stoploss=self.stoploss,
        )

    def confirm_trade_exit(
        self,
        pair: str,
        trade: Trade,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        exit_reason: str,
        current_time: datetime,
        **kwargs,
    ) -> bool:
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

    def _get_candidate_registry(self):
        from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry

        reg = getattr(self, "_candidate_registry", None)
        if reg is None:
            reg = CandidateRegistry()
            self._candidate_registry = reg
        return reg

    def _handle_entry_fill(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        reg,
        trade_id: int,
        side: str,
        enter_tag: str,
    ) -> None:
        from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

        open_ts = getattr(trade, "open_date_utc", None) or current_time
        cid = (
            self._read_persisted_candidate_id(trade)
            or reg.lookup_trade(trade_id)
            or self._resolve_candidate_id(pair, side, enter_tag, open_ts)
        )
        if cid:
            reg.bind_trade(trade_id, cid)
            persisted = self._persist_candidate_id(trade, cid)
            if not persisted:
                logger.warning(
                    f"[CandidateID] candidate_id_persistence_failed "
                    f"trade={trade_id} {pair} id={cid} — "
                    "in-session RAM bind OK, restart correlation NOT safe"
                )
        else:
            logger.warning(
                f"[CandidateID] Entry fill missing id for trade={trade_id} "
                f"{pair} {side} — will record {CANDIDATE_ID_MISSING} on exit"
            )

    @staticmethod
    def _calc_mfe_mae(
        open_rate: float, min_rate: float, max_rate: float, is_short: bool
    ) -> tuple[float, float]:
        if open_rate <= 0:
            return 0.0, 0.0
        if is_short:
            return (open_rate - min_rate) / open_rate, (open_rate - max_rate) / open_rate
        return (max_rate - open_rate) / open_rate, (min_rate - open_rate) / open_rate

    @staticmethod
    def _resolve_exit_pnl(trade: Trade, close_rate: float) -> tuple[float, float]:
        pnl_pct = trade.close_profit
        pnl_usdt = trade.close_profit_abs
        if pnl_pct is None and close_rate > 0 and hasattr(trade, "calc_profit_ratio"):
            pnl_pct = trade.calc_profit_ratio(close_rate)
        if pnl_usdt is None and close_rate > 0 and hasattr(trade, "calc_profit"):
            pnl_usdt = trade.calc_profit(close_rate)
        return float(pnl_pct or 0.0), float(pnl_usdt or 0.0)

    def _handle_exit_fill(
        self,
        pair: str,
        trade: Trade,
        order: Order,
        current_time: datetime,
        reg,
        trade_id: int,
        side: str,
        enter_tag: str,
    ) -> None:
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

        recorded = getattr(self, "_trade_ledger_recorded_ids", None)
        if recorded is None:
            recorded = set()
            self._trade_ledger_recorded_ids = recorded
        if trade_id in recorded:
            return

        try:
            from strategies.mtf_sniper.core.contracts import TelemetryTradeRecord

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
            mfe, mae = self._calc_mfe_mae(open_rate, min_rate, max_rate, trade.is_short)
            pnl_pct, pnl_usdt = self._resolve_exit_pnl(trade, close_rate)

            from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

            open_ts = getattr(trade, "open_date_utc", None) or current_time
            candidate_id = (
                self._read_persisted_candidate_id(trade)
                or reg.lookup_trade(trade_id)
                or self._resolve_candidate_id(pair, side, enter_tag, open_ts)
                or CANDIDATE_ID_MISSING
            )
            if candidate_id == CANDIDATE_ID_MISSING:
                logger.warning(
                    f"[CandidateID] Exit ledger correlation missing for trade={trade_id} "
                    f"{pair} {side}"
                )
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
                    pnl_usdt=pnl_usdt,
                    pnl_pct=pnl_pct,
                    max_favorable_excursion=float(mfe),
                    max_adverse_excursion=float(mae),
                    exit_reason=trade.exit_reason or "",
                )
            )
            recorded.add(trade_id)
            reg.unbind_trade(trade_id)
        except Exception as exc:
            logger.warning(f"[TradeLedger] Failed to record closed trade {pair}: {exc}")

    def order_filled(
        self, pair: str, trade: Trade, order: Order, current_time: datetime, **kwargs
    ) -> None:
        """
        Bind candidate_id on entry fills; persist trade outcomes on exit fills.

        Note: in backtesting, order_filled runs *before* trade.close(), so
        trade.is_open is still True. Detect exits via order side instead.
        """
        exit_side = getattr(trade, "exit_side", None)
        is_exit_fill = order.ft_order_side in (exit_side, "stoploss")
        side = "short" if trade.is_short else "long"
        enter_tag = getattr(trade, "enter_tag", None) or ""
        trade_id = getattr(trade, "id", 0) or 0
        reg = self._get_candidate_registry()

        if not is_exit_fill:
            self._handle_entry_fill(pair, trade, current_time, reg, trade_id, side, enter_tag)
        else:
            self._handle_exit_fill(pair, trade, order, current_time, reg, trade_id, side, enter_tag)
