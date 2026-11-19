"""DB-backed Trade custom-data persistence for Candidate ID (Freqtrade persistence)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from freqtrade.persistence import Trade
from freqtrade.persistence.custom_data import CustomDataWrapper
from tests.conftest import create_mock_trades_usdt

from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    make_candidate_id,
)
from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper


@pytest.mark.usefixtures("init_persistence")
def test_db_backed_persist_survives_registry_clear_on_exit(fee):
    """
    Use real Trade + CustomDataWrapper with DB (init_persistence).

    Entry fill persists candidate_id; after wiping RAM registry, exit fill
    still recovers the same ID from Trade custom data.
    """
    Trade.reset_trades()
    CustomDataWrapper.reset_custom_data()
    create_mock_trades_usdt(fee, is_short=True, use_db=True)

    trade = Trade.get_trades_proxy(is_open=True)[0]
    assert trade.id is not None

    ts = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
    # Align trade identity with the ID we persist
    trade.pair = "BTC/USDT:USDT"
    trade.is_short = True
    trade.enter_tag = "short_A80plus"
    trade.open_date = ts.replace(tzinfo=None)
    trade.amount = 1.0
    trade.open_rate = 100.0
    trade.stake_amount = 100.0
    trade.leverage = 10.0

    cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)

    strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
    strat._candidate_registry = CandidateRegistry()
    strat.enable_telemetry = True
    strat._trade_ledger_recorded_ids = set()
    strat._resolve_candidate_id = lambda *a, **k: cid

    entry_order = SimpleNamespace(ft_order_side="sell", filled=1.0, safe_amount=1.0)
    strat.order_filled("BTC/USDT:USDT", trade, entry_order, ts)
    assert trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY) == cid

    # Restart simulation: empty RAM registry; DB custom data remains
    strat._candidate_registry = CandidateRegistry()
    ledger = MagicMock()
    strat._trade_ledger = ledger
    trade.is_open = True
    trade.close_rate = 95.0
    trade.max_rate = 101.0
    trade.min_rate = 94.0
    trade.close_profit = 0.05
    trade.close_profit_abs = 5.0
    trade.exit_reason = "roi"
    trade.close_date = ts.replace(tzinfo=None)

    exit_order = SimpleNamespace(
        ft_order_side="buy", filled=1.0, safe_amount=1.0, average=95.0
    )
    strat.order_filled("BTC/USDT:USDT", trade, exit_order, ts)

    assert ledger.record.called
    rec = ledger.record.call_args[0][0]
    assert rec.candidate_id == cid
