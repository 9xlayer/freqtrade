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
from strategies.mtf_sniper import STRATEGY_VERSION

logger = logging.getLogger(__name__)


class StrategyMTFSniper(IStrategy):
    """
    MTF Sniper — deterministic layered entry (v4.4.0).

    Entry: part sensors → SetupComposer (AND recipes) → Policy → cooldown.
    Confirm: optional JEV Decision Gate + Risk. Hunter escalation is off by default.
    """

    INTERFACE_VERSION = 3
    strategy_version = STRATEGY_VERSION

    timeframe = "15m"
    can_short: bool = True
    startup_candle_count: int = 400

    # Default fallback flags (overridden by config.json / env vars / runtime mode)
    enable_jev_decision: bool = True
    # Live EntryPipeline ignores Hunter; keep flag false unless explicitly re-enabled.
    enable_jev_hunter: bool = False
    enable_hunter_long: bool = False
    block_macro_bull_shorts: bool = False
    enable_dynamic_sizing: bool = False
    jev_fail_policy: str = "allow"
    mtf_runtime_mode: str = "deterministic"
    _runtime_profile: Optional[object] = None
    _effective_config: Optional[object] = None
    _config_fingerprint: str = ""
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
        1. Runtime mode profile (authoritative for AI/Hunter/Decision mode)
        2. `strategy_params` in config.json
        3. Environment variables (.env / Docker Compose / Coolify)
        4. Built-in defaults
        """
        from strategies.mtf_sniper.core.config_resolve import (
            build_effective_config,
            warn_pair_override_divergences,
        )
        from strategies.mtf_sniper.core.runtime_mode import build_runtime_profile

        strat_cfg = self.config.get("strategy_params", {}) if hasattr(self, "config") and self.config else {}

        # Raw intent before mode clamp
        want_decision = (
            self._to_bool(strat_cfg["enable_jev_decision"], True)
            if "enable_jev_decision" in strat_cfg
            else self._to_bool(os.environ.get("ENABLE_JEV_DECISION", "true"), True)
        )
        want_hunter = (
            self._to_bool(strat_cfg["enable_jev_hunter"], False)
            if "enable_jev_hunter" in strat_cfg
            else self._to_bool(os.environ.get("ENABLE_JEV_HUNTER", "false"), False)
        )
        want_hunter_long = (
            self._to_bool(strat_cfg["enable_hunter_long"], False)
            if "enable_hunter_long" in strat_cfg
            else self._to_bool(os.environ.get("ENABLE_HUNTER_LONG", "false"), False)
        )

        from strategies.mtf_sniper.core.runtime_mode import InvalidRuntimeModeError

        try:
            profile = build_runtime_profile(
                strat_cfg,
                enable_jev_decision_cfg=want_decision,
                enable_jev_hunter_cfg=want_hunter,
                enable_hunter_long_cfg=want_hunter_long,
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
        self.enable_jev_decision = bool(profile.enable_jev_decision)
        self.enable_jev_hunter = bool(profile.enable_jev_hunter)
        self.enable_hunter_long = bool(profile.enable_hunter_long)
        # Decision mode is passed explicitly to JevDecisionAdapter — do not mutate
        # process-global os.environ["JEV_MODE"] (cross-strategy / multi-instance safe).

        # Macro Bull Short Guard
        if "block_macro_bull_shorts" in strat_cfg:
            self.block_macro_bull_shorts = self._to_bool(strat_cfg["block_macro_bull_shorts"], False)
        else:
            self.block_macro_bull_shorts = self._to_bool(os.environ.get("BLOCK_MACRO_BULL_SHORTS", "false"), False)

        # Dynamic Stake Sizing
        if "enable_dynamic_sizing" in strat_cfg:
            self.enable_dynamic_sizing = self._to_bool(strat_cfg["enable_dynamic_sizing"], False)
        else:
            self.enable_dynamic_sizing = self._to_bool(os.environ.get("ENABLE_DYNAMIC_SIZING", "false"), False)

        # JEV Fail Policy ('reject' / 'allow')
        if "jev_fail_policy" in strat_cfg:
            self.jev_fail_policy = str(strat_cfg["jev_fail_policy"]).lower()
        else:
            self.jev_fail_policy = os.environ.get("JEV_FAIL_POLICY", "allow").lower()

        if "enable_telemetry" in strat_cfg:
            self.enable_telemetry = self._to_bool(strat_cfg["enable_telemetry"], True)
        else:
            self.enable_telemetry = self._to_bool(os.environ.get("ENABLE_TELEMETRY", "true"), True)

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
        if self._is_hyperopt_run() or not bool(self.enable_telemetry):
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
            f"Hunter={self.enable_jev_hunter} (long_promote={self.enable_hunter_long}) "
            f"block_macro_bull_shorts={self.block_macro_bull_shorts} "
            f"dynamic_sizing={self.enable_dynamic_sizing} "
            f"telemetry={self.enable_telemetry}"
        )
        if eff is not None:
            logger.info(f"[EffectiveConfig] {eff.to_log_dict()}")
        api_key = os.environ.get("JEV_API_KEY", "")
        if getattr(profile, "allow_external_ai", False) and (
            self.enable_jev_decision or self.enable_jev_hunter
        ):
            if api_key:
                masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
                logger.info(
                    f"[JEV] API key detected ({masked}). "
                    f"URL: {os.environ.get('JEV_API_URL', 'https://api.typesafe.ai/v1/systemone')}"
                )
            else:
                logger.warning(
                    "[JEV] JEV_API_KEY unset — external Decision/Hunter unavailable."
                )
        elif self.enable_jev_decision:
            logger.info(
                "[JEV] Deterministic/native Decision Gate only — no external AI inference."
            )
        else:
            logger.info("[JEV] Decision and Hunter disabled — pure technical path.")

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
            )
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

    # Long Confluence Parameters (HO Ep46 effective set, 2026-11-18).
    # body/dist defaults match custom_pair_params (those pair keys win in setups).
    long_loc_tolerance_1h = DecimalParameter(0.980, 1.010, default=0.988, decimals=3, space="buy", optimize=True)
    long_max_1h_rsi_exhaustion = DecimalParameter(40.0, 68.0, default=57.1, decimals=1, space="buy", optimize=True)
    long_lookback_resistance = IntParameter(8, 25, default=15, space="buy", optimize=True)
    long_candle_body_ratio_min = DecimalParameter(0.40, 0.70, default=0.63, decimals=2, space="buy", optimize=True)
    long_max_dist_ema21 = DecimalParameter(1.0, 3.5, default=3.3, decimals=1, space="buy", optimize=True)
    long_max_rsi_entry = DecimalParameter(65.0, 85.0, default=68.1, decimals=1, space="buy", optimize=True)
    long_cooldown_candles = IntParameter(16, 96, default=40, space="buy", optimize=True)
    long_overextended_dist_max = DecimalParameter(0.35, 1.00, default=0.53, decimals=2, space="buy", optimize=True)
    long_block_1d_macd = BooleanParameter(default=False, space="buy", optimize=True)

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
                profile = self._runtime_profile
                force_native = not bool(getattr(profile, "allow_external_ai", False))
                mode = getattr(profile, "decision_jev_mode", "native") if profile else "native"
                self._decision_adapter = JevDecisionAdapter(
                    force_native=force_native,
                    mode=mode,
                )
            decision = self._decision_adapter

            analyzed_df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if analyzed_df is None or analyzed_df.empty:
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: missing analyzed dataframe."
                    )
                    return False
                return True

            # Decision only needs lookback bars + last-row macro (not full history).
            lookback = 32
            up_to = (analyzed_df["date"] <= current_time).to_numpy().nonzero()[0]
            if len(up_to) == 0:
                df_window = analyzed_df.iloc[0:0]
            else:
                end_pos = int(up_to[-1])
                start_pos = max(0, end_pos - lookback + 1)
                df_window = analyzed_df.iloc[start_pos : end_pos + 1]
            if len(df_window) < 16:
                if getattr(self, "jev_fail_policy", "allow") in ("reject", "fail_closed", "safe"):
                    logger.warning(
                        f"[JEV AI] Fail-safe active -> Trade {pair} rejected: insufficient candle history (<16 bars)."
                    )
                    return False
                return True

            payload = decision.build_payload(df_window, pair, side, lookback=lookback)
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

    def _resolve_candidate_id(
        self,
        pair: str,
        side: str,
        entry_tag: str | None,
        current_time: datetime,
    ) -> str | None:
        """Registry → reconstruct from analyzed enter bar. Never invent non-canonical IDs."""
        from strategies.mtf_sniper.core.candidate_id import make_candidate_id, normalize_utc
        from strategies.mtf_sniper.core.enums import TradeSide

        reg = getattr(self, "_candidate_registry", None)
        if reg is not None:
            found = reg.lookup(
                pair=pair, side=side, entry_tag=entry_tag, current_time=current_time
            )
            if found:
                return found

        # Reconstruct canonical ID from last matching enter_* bar
        try:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        except Exception:
            df = None
        if df is not None and not df.empty and "date" in df.columns:
            side_l = str(side).lower()
            col = "enter_short" if side_l == "short" else "enter_long"
            if col in df.columns:
                mask = (df["date"] <= current_time) & (df[col].fillna(0).astype(bool))
                if entry_tag and "enter_tag" in df.columns:
                    mask = mask & (df["enter_tag"].astype(str) == str(entry_tag))
                hits = df.loc[mask]
                if not hits.empty:
                    label = hits.index[-1]
                    row = hits.iloc[-1]
                    candle_index = self._dataframe_position(df, label)
                    if candle_index is None:
                        logger.warning(
                            f"[CandidateID] Cannot resolve candle position for "
                            f"{pair} label={label!r} — refusing non-canonical invent"
                        )
                        return None
                    ts = normalize_utc(row["date"])
                    trade_side = TradeSide.SHORT if side_l == "short" else TradeSide.LONG
                    return make_candidate_id(pair, trade_side, ts, candle_index)

        return None

    @staticmethod
    def _dataframe_position(df, label) -> int | None:
        """Absolute row position in ``df`` for ``label`` — never hits-relative."""
        try:
            indexer = df.index.get_indexer([label])
            if len(indexer) and int(indexer[0]) >= 0:
                return int(indexer[0])
        except Exception:
            pass
        try:
            loc = df.index.get_loc(label)
            if isinstance(loc, (int,)):
                return int(loc)
            if isinstance(loc, slice):
                # Duplicate labels → last occurrence in the slice
                return int(loc.stop - 1) if loc.stop else int(loc.start)
            # ndarray / boolean mask from duplicate labels
            import numpy as np

            arr = np.asarray(loc)
            if arr.dtype == bool:
                nz = np.flatnonzero(arr)
                return int(nz[-1]) if len(nz) else None
            return int(arr.ravel()[-1])
        except Exception:
            return None

    def _persist_candidate_id(self, trade: Trade, candidate_id: str | None) -> bool:
        """Write + re-read verify. Returns True only when restart-safe persist succeeded."""
        from strategies.mtf_sniper.core.candidate_id import (
            CANDIDATE_ID_CUSTOM_DATA_KEY,
            CANDIDATE_ID_MISSING,
            is_canonical_candidate_id,
        )

        if not candidate_id or candidate_id == CANDIDATE_ID_MISSING:
            return False
        if not is_canonical_candidate_id(candidate_id):
            logger.warning(
                f"[CandidateID] Refusing to persist non-canonical id={candidate_id!r}"
            )
            return False
        try:
            trade.set_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY, candidate_id)
        except Exception as exc:
            logger.warning(
                f"[CandidateID] candidate_id_persistence_failed set_custom_data: {exc}"
            )
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
            logger.warning(
                f"[CandidateID] Ignoring non-canonical persisted id={cid!r}"
            )
            return None
        return cid

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
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
        pair_cfg = self._get_pair_params(pair)
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
            candidate_id=candidate_id or CANDIDATE_ID_MISSING,
            setup_type=str(entry_tag or ""),
            signal_source="confirm_trade_entry",
            default_stoploss=float(pair_cfg.get("stoploss", self.stoploss)),
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
    def _get_candidate_registry(self):
        from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry

        reg = getattr(self, "_candidate_registry", None)
        if reg is None:
            reg = CandidateRegistry()
            self._candidate_registry = reg
        return reg

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
        trade_id = int(getattr(trade, "id", 0) or 0)
        reg = self._get_candidate_registry()

        if not is_exit_fill:
            # Entry fill — persist on Trade custom data (restart-safe) + RAM bind.
            from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

            open_ts = getattr(trade, "open_date_utc", None) or current_time
            cid = (
                self._read_persisted_candidate_id(trade)
                or reg.lookup_trade(trade_id)
                or self._resolve_candidate_id(pair, side, enter_tag, open_ts)
            )
            if cid:
                # RAM bind for in-session correlation even if durable persist fails.
                reg.bind_trade(trade_id, cid)
                persisted = self._persist_candidate_id(trade, cid)
                if not persisted:
                    logger.warning(
                        f"[CandidateID] candidate_id_persistence_failed "
                        f"trade={trade_id} {pair} id={cid} — "
                        f"in-session RAM bind OK, restart correlation NOT safe"
                    )
            else:
                logger.warning(
                    f"[CandidateID] Entry fill missing id for trade={trade_id} "
                    f"{pair} {side} — will record {CANDIDATE_ID_MISSING} on exit"
                )
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

            from strategies.mtf_sniper.core.candidate_id import CANDIDATE_ID_MISSING

            open_ts = getattr(trade, "open_date_utc", None) or current_time
            # Prefer Trade custom data (survives restart) over RAM registry.
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
                    pnl_usdt=float(pnl_usdt or 0.0),
                    pnl_pct=float(pnl_pct or 0.0),
                    max_favorable_excursion=float(mfe),
                    max_adverse_excursion=float(mae),
                    exit_reason=str(trade.exit_reason or ""),
                )
            )
            recorded.add(trade_id)
            reg.unbind_trade(trade_id)
        except Exception as exc:
            logger.warning(f"[TradeLedger] Failed to record closed trade {pair}: {exc}")
