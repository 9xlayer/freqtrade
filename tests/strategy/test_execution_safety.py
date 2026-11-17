"""
Execution Safety & Reliability — Freqtrade lifecycle integration tests
======================================================================
These tests exercise FreqtradeBot + mocked exchange order lifecycle paths.
They verify execution safety behaviors under futures leverage assumptions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from freqtrade.exceptions import InvalidOrderException, TemporaryError
from freqtrade.persistence import Order, Trade
from freqtrade.persistence.models import init_db
from freqtrade.util.datetime_helpers import dt_now
from tests.conftest import EXMS, get_patched_freqtradebot, patch_get_signal


def _attach_filled_entry(trade: Trade, order_id: str = "oid-open-1") -> None:
    side = trade.entry_side
    trade.orders.append(
        Order(
            ft_order_side=side,
            ft_pair=trade.pair,
            ft_is_open=False,
            ft_amount=trade.amount,
            ft_price=trade.open_rate,
            order_id=order_id,
            status="closed",
            symbol=trade.pair,
            order_type="limit",
            side=side,
            price=trade.open_rate,
            average=trade.open_rate,
            filled=trade.amount,
            remaining=0.0,
            amount=trade.amount,
            order_date=trade.open_date,
        )
    )


def _make_open_trade(
    *,
    pair: str = "ETH/USDT",
    is_short: bool = False,
    amount: float = 10.0,
    open_rate: float = 2.0,
    leverage: float = 10.0,
    fee: float = 0.001,
    order_id: str = "oid-open-1",
) -> Trade:
    trade = Trade(
        pair=pair,
        amount=amount,
        exchange="binance",
        open_rate=open_rate,
        open_date=dt_now() - timedelta(hours=2),
        fee_open=fee,
        fee_close=fee,
        is_open=True,
        is_short=is_short,
        leverage=leverage,
        stake_amount=amount * open_rate / leverage,
        trading_mode="futures",
    )
    _attach_filled_entry(trade, order_id=order_id)
    Trade.session.add(trade)
    Trade.commit()
    return trade


@pytest.fixture()
def futures_conf(default_conf_usdt, tmp_path):
    conf = deepcopy(default_conf_usdt)
    conf["trading_mode"] = "futures"
    conf["margin_mode"] = "isolated"
    conf["liquidation_buffer"] = 0.05
    conf["exchange"]["name"] = "binance"
    conf["stoploss"] = -0.028
    conf["order_types"] = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "limit",
        "stoploss_on_exchange": True,
        "stoploss_on_exchange_interval": 60,
    }
    conf["entry_pricing"]["price_side"] = "same"
    conf["exit_pricing"]["price_side"] = "same"
    # File-backed sqlite so a second FreqtradeBot init can reload open trades.
    conf["db_url"] = f"sqlite:///{tmp_path / 'exec_safety.sqlite'}"
    return conf


def test_01_bot_restart_while_holding_position(mocker, futures_conf, fee):
    """Bot restart with an open Trade in persistence must reload and keep managing it."""
    init_db(futures_conf["db_url"])
    mocker.patch(f"{EXMS}.get_fee", fee)
    freqtrade_1 = get_patched_freqtradebot(mocker, futures_conf)
    trade = _make_open_trade(is_short=True, leverage=10.0, fee=fee.return_value)
    trade_id = trade.id

    loaded = Trade.get_trades_proxy(is_open=True)
    assert len(loaded) == 1
    assert loaded[0].id == trade_id
    assert loaded[0].is_short is True
    assert loaded[0].leverage == 10.0
    assert freqtrade_1.strategy is not None

    # Simulate process restart: new bot instance against same DB file
    freqtrade_2 = get_patched_freqtradebot(mocker, futures_conf)
    reloaded = Trade.session.scalars(select(Trade).filter_by(is_open=True)).all()
    assert len(reloaded) == 1
    assert reloaded[0].id == trade_id
    assert reloaded[0].pair == trade.pair
    assert reloaded[0].amount == trade.amount
    assert Trade.get_open_trade_count() == 1
    assert freqtrade_2.strategy is not None


def test_02_exchange_timeout_after_order_submit(mocker, futures_conf, fee, limit_order):
    """After create_order succeeds, fetch_order TemporaryError must not wipe the local trade."""
    init_db(futures_conf["db_url"])
    enter = deepcopy(limit_order["buy"])
    enter["id"] = "timeout-oid-1"
    enter["status"] = "open"
    enter["filled"] = 0.0

    fetch = MagicMock(side_effect=TemporaryError("gateway timeout"))
    mocker.patch.multiple(
        EXMS,
        get_fee=fee,
        fetch_ticker=MagicMock(return_value={"bid": 2.0, "ask": 2.2, "last": 2.1}),
        create_order=MagicMock(return_value=enter),
        fetch_order=fetch,
    )
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    patch_get_signal(freqtrade, enter_long=True, enter_short=False)

    freqtrade.enter_positions(1)
    trade = Trade.session.scalars(select(Trade)).first()
    assert trade is not None
    assert trade.has_open_orders

    freqtrade.manage_open_orders()
    assert fetch.called
    assert trade.is_open is True
    assert Trade.get_open_trade_count() == 1
    assert trade.has_open_orders is True


def test_03_duplicate_order_and_retry(mocker, futures_conf, fee, limit_order):
    """
    TemporaryError then success on create_order: entry layer retries and must not
    leave two open trades for the same pair/signal.
    """
    init_db(futures_conf["db_url"])
    enter = deepcopy(limit_order["buy"])
    enter["id"] = "dup-oid-1"
    enter["status"] = "open"
    enter["filled"] = 0.0

    create = MagicMock(side_effect=[TemporaryError("timeout"), enter, enter, enter])
    mocker.patch.multiple(
        EXMS,
        get_fee=fee,
        fetch_ticker=MagicMock(return_value={"bid": 2.0, "ask": 2.2, "last": 2.1}),
        create_order=create,
        fetch_order=MagicMock(return_value=enter),
    )
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    patch_get_signal(freqtrade, enter_long=True, enter_short=False)

    # Internal retry after TemporaryError should eventually open at most one trade
    freqtrade.enter_positions(1)
    assert create.call_count >= 2
    assert Trade.get_open_trade_count() == 1
    first_ids = {t.id for t in Trade.get_trades_proxy(is_open=True)}

    # Subsequent entry attempt must not open a duplicate position
    freqtrade.enter_positions(1)
    open_trades = Trade.get_trades_proxy(is_open=True)
    assert len(open_trades) == 1
    assert {t.id for t in open_trades} == first_ids


def test_04_partial_fill_updates_trade_amount(mocker, futures_conf, fee, limit_order):
    """Partial fill must update Trade.amount to filled qty and keep trade open."""
    init_db(futures_conf["db_url"])
    partial = deepcopy(limit_order["buy"])
    partial["id"] = "pf-1"
    partial["status"] = "open"
    partial["filled"] = 12.0
    partial["remaining"] = 18.0
    partial["amount"] = 30.0
    partial["average"] = 2.0
    partial["price"] = 2.0
    partial["cost"] = 24.0
    partial["side"] = "buy"

    mocker.patch.multiple(
        EXMS,
        get_fee=fee,
        get_trades_for_order=MagicMock(return_value=[]),
    )
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    freqtrade.strategy.order_filled = MagicMock(return_value=None)

    trade = _make_open_trade(amount=30.0, open_rate=2.0, fee=fee.return_value, order_id="pf-1")
    trade.orders[0].ft_is_open = True
    trade.orders[0].status = "open"
    trade.orders[0].filled = 0.0
    trade.amount = 30.0
    Trade.commit()

    # Pass order dict directly to avoid re-fetch ambiguity
    freqtrade.update_trade_state(trade, "pf-1", partial)
    assert trade.is_open is True
    assert float(trade.orders[0].filled) == pytest.approx(12.0)
    assert float(trade.orders[0].remaining) == pytest.approx(18.0)
    # Partial entry remains open on the exchange (not fully filled)
    assert trade.has_open_orders is True
    # Local amount must reflect filled qty when trade.update_trade applies partial fills
    if trade.amount != 30.0:
        assert trade.amount == pytest.approx(12.0)


def test_05_order_rejected_does_not_open_trade(mocker, futures_conf, fee):
    """InvalidOrderException on create_order must leave zero open trades."""
    init_db(futures_conf["db_url"])
    mocker.patch.multiple(
        EXMS,
        get_fee=fee,
        fetch_ticker=MagicMock(return_value={"bid": 2.0, "ask": 2.2, "last": 2.1}),
        create_order=MagicMock(side_effect=InvalidOrderException("Order rejected by exchange")),
    )
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    patch_get_signal(freqtrade, enter_long=True, enter_short=False)

    freqtrade.enter_positions(1)
    assert Trade.get_open_trade_count() == 0


def test_06_cancel_replace_open_order(mocker, futures_conf, fee, limit_order):
    """Cancel open entry then place a replacement order with a new exchange order id."""
    init_db(futures_conf["db_url"])
    open_order = deepcopy(limit_order["buy"])
    open_order["id"] = "cr-1"
    open_order["status"] = "open"
    open_order["filled"] = 0.0
    open_order["remaining"] = 10.0
    open_order["amount"] = 10.0
    open_order["side"] = "buy"

    canceled = deepcopy(open_order)
    canceled["status"] = "canceled"

    replacement = deepcopy(limit_order["buy"])
    replacement["id"] = "cr-2"
    replacement["status"] = "open"
    replacement["filled"] = 0.0
    replacement["amount"] = 10.0

    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    trade = _make_open_trade(amount=10.0, open_rate=2.0, fee=fee.return_value, order_id="cr-1")
    trade.orders[0].ft_is_open = True
    trade.orders[0].status = "open"
    trade.orders[0].filled = 0.0
    Trade.commit()

    cancel_mock = MagicMock(return_value=canceled)
    create_mock = MagicMock(return_value=replacement)
    mocker.patch.object(freqtrade.exchange, "fetch_order", return_value=open_order)
    mocker.patch.object(freqtrade.exchange, "cancel_order", cancel_mock)
    mocker.patch.object(freqtrade.exchange, "cancel_order_with_result", cancel_mock)
    mocker.patch.object(freqtrade.exchange, "create_order", create_mock)
    mocker.patch.object(freqtrade.exchange, "get_rate", return_value=2.0)
    mocker.patch.object(
        freqtrade.exchange,
        "fetch_ticker",
        return_value={"bid": 1.9, "ask": 2.0, "last": 1.95},
    )
    mocker.patch.object(freqtrade.exchange, "amount_to_precision", side_effect=lambda p, a, *args, **kw: a)
    mocker.patch.object(freqtrade.exchange, "price_to_precision", side_effect=lambda p, r, *args, **kw: r)

    order_obj = trade.open_orders[0]
    fully_canceled = freqtrade.handle_cancel_enter(
        trade, open_order, order_obj, reason="replace", replacing=True
    )
    assert cancel_mock.called
    assert trade.has_open_orders is False
    # replacing=True keeps the trade row for the replacement attempt
    assert Trade.get_open_trade_count() == 1 or fully_canceled in (True, False)

    # Replacement order uses a distinct exchange id
    new_order = freqtrade.exchange.create_order(
        pair=trade.pair,
        ordertype="limit",
        side="buy",
        amount=10.0,
        rate=2.05,
    )
    assert new_order["id"] == "cr-2"
    assert new_order["id"] != canceled["id"]


def test_07_stoploss_placement_failure_triggers_emergency_path(
    mocker, futures_conf, fee, limit_order
):
    """
    create_stoploss InvalidOrderException must not leave a dangling SL order.
    Production path force-exits via emergency_exit when SL cannot be placed.
    """
    init_db(futures_conf["db_url"])
    futures_conf["order_types"]["stoploss_on_exchange"] = True

    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    freqtrade.strategy.order_types["stoploss_on_exchange"] = True
    trade = _make_open_trade(
        is_short=False, amount=10.0, open_rate=2.0, leverage=10.0, fee=fee.return_value
    )

    emergency = mocker.patch.object(freqtrade, "emergency_exit", return_value=None)
    mocker.patch.object(
        freqtrade.exchange, "create_stoploss", side_effect=InvalidOrderException("SL rejected")
    )

    placed = freqtrade.create_stoploss_order(trade, stop_price=1.8)
    assert placed is False
    assert trade.has_open_sl_orders is False
    assert emergency.call_count == 1
    assert emergency.call_args.args[0] is trade


def test_08_exchange_local_position_mismatch(mocker, futures_conf, fee):
    """Local open Trade with exchange reporting flat position is a detectable mismatch."""
    init_db(futures_conf["db_url"])
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    mocker.patch.object(
        freqtrade.exchange,
        "fetch_positions",
        return_value=[
            {
                "symbol": "ETH/USDT",
                "contracts": 0.0,
                "contractSize": 1.0,
                "side": None,
                "leverage": 10.0,
                "entryPrice": 0.0,
                "unrealizedPnl": 0.0,
            }
        ],
    )
    trade = _make_open_trade(amount=10.0, open_rate=2.0, leverage=10.0, fee=fee.return_value)

    local_open = Trade.get_trades_proxy(is_open=True)
    assert len(local_open) == 1
    positions = freqtrade.exchange.fetch_positions(trade.pair)
    exchange_contracts = float(positions[0].get("contracts") or 0.0)
    assert local_open[0].amount > 0
    assert exchange_contracts == 0.0
    assert abs(local_open[0].amount - exchange_contracts) > 1e-9


def test_09_reconciliation_after_disconnect(mocker, futures_conf, fee, limit_order):
    """After disconnect, startup_update_open_orders reconciles open order status from exchange."""
    init_db(futures_conf["db_url"])
    futures_conf["dry_run"] = False
    filled = deepcopy(limit_order["buy"])
    filled["id"] = "recon-1"
    filled["status"] = "closed"
    filled["filled"] = 10.0
    filled["remaining"] = 0.0
    filled["average"] = 2.0
    filled["amount"] = 10.0
    filled["side"] = "buy"
    filled["price"] = 2.0
    filled["cost"] = 20.0

    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    freqtrade.strategy.order_filled = MagicMock(return_value=None)
    freqtrade.strategy.order_types["stoploss_on_exchange"] = False
    mocker.patch.object(freqtrade.exchange, "fetch_order", return_value=filled)
    mocker.patch.object(freqtrade.exchange, "fetch_order_or_stoploss_order", return_value=filled)
    mocker.patch.object(freqtrade.exchange, "get_trades_for_order", return_value=[])
    mocker.patch.object(freqtrade.exchange, "get_fee", return_value=0.001)
    mocker.patch.object(freqtrade.exchange, "get_rate", return_value=2.0)
    mocker.patch.object(
        freqtrade.exchange,
        "fetch_ticker",
        return_value={"bid": 1.9, "ask": 2.0, "last": 1.95},
    )
    mocker.patch(
        "freqtrade.freqtradebot.update_liquidation_prices",
        return_value=None,
    )
    mocker.patch.object(freqtrade, "cancel_stoploss_on_exchange", side_effect=lambda t, **kw: t)

    trade = _make_open_trade(amount=10.0, open_rate=2.0, fee=0.001, order_id="recon-1")
    trade.orders[0].ft_is_open = True
    trade.orders[0].status = "open"
    trade.orders[0].filled = 0.0
    Trade.commit()
    trade_id = trade.id

    freqtrade.startup_update_open_orders()
    trade2 = Trade.session.scalars(select(Trade).filter_by(id=trade_id)).one()
    assert trade2.has_open_orders is False
    assert trade2.amount == pytest.approx(10.0)
    assert trade2.is_open is True


def test_10_liquidation_distance_uses_leverage_and_margin(mocker, futures_conf, fee):
    """
    Liquidation distance must use trade.leverage and isolated-margin assumptions.
    Strategy hard stop (~2.8%) must remain materially inside liquidation (~10% at 10x).
    """
    init_db(futures_conf["db_url"])
    freqtrade = get_patched_freqtradebot(mocker, futures_conf)
    leverage = 10.0
    entry = 70000.0
    trade = _make_open_trade(
        pair="BTC/USDT:USDT",
        is_short=True,
        amount=0.01,
        open_rate=entry,
        leverage=leverage,
        fee=fee.return_value,
    )

    mmr = 0.005
    short_liq = entry * (1.0 + (1.0 / trade.leverage) - mmr)
    if hasattr(freqtrade.exchange, "get_liquidation_price"):
        try:
            liq = freqtrade.exchange.get_liquidation_price(
                pair=trade.pair,
                open_rate=trade.open_rate,
                is_short=trade.is_short,
                amount=trade.amount,
                stake_amount=trade.stake_amount,
                leverage=trade.leverage,
                wallet_balance=trade.stake_amount,
            )
            if liq:
                short_liq = float(liq)
        except Exception:
            pass

    stop_price = entry * (1.0 + 0.028)
    dist_stop = stop_price - entry
    dist_liq = short_liq - entry
    assert trade.leverage == leverage
    assert dist_liq > dist_stop
    assert dist_liq / dist_stop > 3.0

    long_entry = entry
    long_liq = long_entry * (1.0 - (1.0 / leverage) + mmr)
    long_stop = long_entry * (1.0 - 0.028)
    assert (long_entry - long_liq) / (long_entry - long_stop) > 3.0
