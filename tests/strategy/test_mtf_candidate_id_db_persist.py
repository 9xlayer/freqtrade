"""DB-backed Trade custom-data persistence for Candidate ID (Freqtrade persistence).

These tests prove Trade custom-data survives ORM expire/reload via a fresh
session query. They are **not** a full live-bot restart (process recycle /
Docker restart) — that remains an operational verification gap.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from freqtrade.persistence import Order, Trade
from freqtrade.persistence.custom_data import CustomDataWrapper
from strategies.mtf_sniper.core.candidate_id import (
    CANDIDATE_ID_CUSTOM_DATA_KEY,
    make_candidate_id,
)
from strategies.mtf_sniper.core.candidate_registry import CandidateRegistry
from strategies.mtf_sniper.core.enums import TradeSide
from strategies.mtf_sniper.strategy_mtf_sniper import StrategyMTFSniper
from tests.conftest import create_mock_trades_usdt


def _prepare_short_trade(trade: Trade, ts: datetime) -> None:
    trade.pair = "BTC/USDT:USDT"
    trade.is_short = True
    trade.enter_tag = "short_A80_plus"
    trade.open_date = ts.replace(tzinfo=None)
    trade.amount = 1.0
    trade.open_rate = 100.0
    trade.stake_amount = 100.0
    trade.leverage = 10.0


@pytest.mark.usefixtures("init_persistence")
def test_db_backed_persist_survives_registry_clear_on_exit(fee):
    """Entry fill persists; after wiping RAM registry, exit fill recovers ID."""
    Trade.reset_trades()
    CustomDataWrapper.reset_custom_data()
    create_mock_trades_usdt(fee, is_short=True, use_db=True)

    trade = Trade.get_trades_proxy(is_open=True)[0]
    assert trade.id is not None

    ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    _prepare_short_trade(cast(Trade, trade), ts)
    cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 42)

    strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
    strat._candidate_registry = CandidateRegistry()
    strat.enable_telemetry = True
    strat._trade_ledger_recorded_ids = set()
    strat._resolve_candidate_id = lambda *a, **k: cid

    entry_order = SimpleNamespace(ft_order_side="sell", filled=1.0, safe_amount=1.0)
    strat.order_filled(
        "BTC/USDT:USDT", cast(Trade, trade), cast(Order, entry_order), ts
    )
    assert trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY) == cid

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
    strat.order_filled(
        "BTC/USDT:USDT", cast(Trade, trade), cast(Order, exit_order), ts
    )

    assert ledger.record.called
    rec = ledger.record.call_args[0][0]
    assert rec.candidate_id == cid


@pytest.mark.usefixtures("init_persistence")
def test_db_expire_reload_preserves_candidate_id_for_exit_ledger(fee):
    """
    Persist → commit → expire session → reload Trade by id in a new query →
    verify custom data → exit ledger with empty RAM registry.
    """
    Trade.reset_trades()
    CustomDataWrapper.reset_custom_data()
    create_mock_trades_usdt(fee, is_short=True, use_db=True)

    trade = Trade.get_trades_proxy(is_open=True)[0]
    assert trade.id is not None
    trade_id = trade.id
    ts = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
    _prepare_short_trade(cast(Trade, trade), ts)
    cid = make_candidate_id("BTC/USDT:USDT", TradeSide.SHORT, ts, 99)

    strat = StrategyMTFSniper.__new__(StrategyMTFSniper)
    strat._candidate_registry = CandidateRegistry()
    strat.enable_telemetry = True
    strat._trade_ledger_recorded_ids = set()
    strat._resolve_candidate_id = lambda *a, **k: cid

    entry_order = SimpleNamespace(ft_order_side="sell", filled=1.0, safe_amount=1.0)
    strat.order_filled(
        "BTC/USDT:USDT", cast(Trade, trade), cast(Order, entry_order), ts
    )
    assert trade.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY) == cid

    # Flush identity fields + custom data to DB, then drop ORM identity map.
    Trade.commit()
    Trade.session.expire_all()

    reloaded = Trade.session.get(Trade, trade_id)
    assert reloaded is not None
    assert reloaded.get_custom_data(CANDIDATE_ID_CUSTOM_DATA_KEY) == cid
    # Strategy read path must also accept reloaded trade
    assert StrategyMTFSniper._read_persisted_candidate_id(strat, reloaded) == cid

    # Fresh strategy RAM registry — exit must still correlate via custom data
    strat._candidate_registry = CandidateRegistry()
    ledger = MagicMock()
    strat._trade_ledger = ledger
    reloaded.is_open = True
    reloaded.close_rate = 95.0
    reloaded.max_rate = 101.0
    reloaded.min_rate = 94.0
    reloaded.close_profit = 0.05
    reloaded.close_profit_abs = 5.0
    reloaded.exit_reason = "roi"
    reloaded.close_date = ts.replace(tzinfo=None)

    exit_order = SimpleNamespace(
        ft_order_side="buy", filled=1.0, safe_amount=1.0, average=95.0
    )
    strat.order_filled(
        "BTC/USDT:USDT", reloaded, cast(Order, exit_order), ts
    )

    assert ledger.record.called
    assert ledger.record.call_args[0][0].candidate_id == cid
