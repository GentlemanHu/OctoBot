#  This file is part of OctoBot (https://github.com/Drakkar-Software/OctoBot)
#  Copyright (c) 2023 Drakkar-Software, All rights reserved.
#
#  OctoBot is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either
#  version 3.0 of the License, or (at your option) any later version.
#
#  OctoBot is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  General Public License for more details.
#
#  You should have received a copy of the GNU General Public
#  License along with OctoBot. If not, see <https://www.gnu.org/licenses/>.
import contextlib
import decimal
import pytest

import octobot_trading.enums as trading_enums
import octobot_trading.constants as trading_constants
import octobot_trading.errors as trading_errors
import trading_backend.enums
from additional_tests.exchanges_tests import abstract_authenticated_exchange_tester


class AbstractAuthenticatedFutureExchangeTester(
    abstract_authenticated_exchange_tester.AbstractAuthenticatedExchangeTester
):
    # enter exchange name as a class variable here*
    EXCHANGE_TYPE = trading_enums.ExchangeTypes.FUTURE.value
    PORTFOLIO_TYPE_FOR_SIZE = trading_constants.CONFIG_PORTFOLIO_TOTAL
    INVERSE_SYMBOL = None
    MIN_PORTFOLIO_SIZE = 2  # ensure fetching currency for linear and inverse
    SUPPORTS_GET_LEVERAGE = True
    SUPPORTS_EMPTY_POSITION_SET_MARGIN_TYPE = True

    async def test_get_empty_linear_and_inverse_positions(self):
        # ensure fetch empty positions
        async with self.local_exchange_manager():
            await self.inner_test_get_empty_linear_and_inverse_positions()

    async def inner_test_get_empty_linear_and_inverse_positions(self):
        if self.exchange_manager.exchange.SUPPORTS_SET_MARGIN_TYPE:
            await self.set_margin_type(trading_enums.MarginType.ISOLATED)
            await self._inner_test_get_empty_linear_and_inverse_positions_for_margin_type(
                trading_enums.MarginType.ISOLATED
            )
            await self.set_margin_type(trading_enums.MarginType.CROSS)
            await self._inner_test_get_empty_linear_and_inverse_positions_for_margin_type(
                trading_enums.MarginType.CROSS
            )
        else:
            await self._inner_test_get_empty_linear_and_inverse_positions_for_margin_type(None)

    async def _inner_test_get_empty_linear_and_inverse_positions_for_margin_type(
        self, margin_type: trading_enums.MarginType
    ):
        positions = await self.get_positions()
        self._check_positions_content(positions)
        position = await self.get_position(self.SYMBOL)
        self._check_position_content(position, self.SYMBOL, margin_type=margin_type)
        for contract_type in (trading_enums.FutureContractType.LINEAR_PERPETUAL,
                              trading_enums.FutureContractType.INVERSE_PERPETUAL):
            if not self.has_empty_position(self.get_filtered_positions(positions, contract_type)):
                empty_position_symbol = self.get_other_position_symbol(positions, contract_type)
                # test with get_position
                empty_position = await self.get_position(empty_position_symbol)
                assert self.is_position_empty(empty_position)
                # test with get_positions
                empty_positions = await self.get_positions([empty_position_symbol])
                assert len(empty_positions) == 1
                assert self.is_position_empty(empty_positions[0])

    async def test_get_and_set_leverage(self):
        # ensure set_leverage works
        async with self.local_exchange_manager():
            await self.inner_test_get_and_set_leverage()

    async def inner_test_get_and_set_leverage(self):
        contract = await self.init_and_get_contract()
        origin_margin_type = contract.margin_type
        origin_leverage = contract.current_leverage
        assert origin_leverage != trading_constants.ZERO
        if self.SUPPORTS_GET_LEVERAGE:
            assert origin_leverage == await self.get_leverage()
        new_leverage = origin_leverage + 1
        if not self.exchange_manager.exchange.supports_api_leverage_update(self.SYMBOL):
            # can't set from api: make sure of that
            with pytest.raises(trading_errors.NotSupported):
                await self.exchange_manager.exchange.connector.set_symbol_leverage(self.SYMBOL, float(new_leverage))
            return
        await self.set_leverage(new_leverage)
        await self._check_margin_type_and_leverage(origin_margin_type, new_leverage)    # did not change margin type
        # change leverage back to origin value
        await self.set_leverage(origin_leverage)
        await self._check_margin_type_and_leverage(origin_margin_type, origin_leverage)  # did not change margin type

    async def inner_test_get_max_orders_count(self):
        self._test_symbol_max_orders_count(self.SYMBOL)
        self._test_symbol_max_orders_count(self.INVERSE_SYMBOL)

    async def test_get_and_set_margin_type(self):
        # ensure set_leverage works
        async with self.local_exchange_manager():
            await self.inner_test_get_and_set_margin_type(allow_empty_position=True)

    async def inner_test_get_and_set_margin_type(self, allow_empty_position=False, symbol=None, has_open_position=False):
        contract = await self.init_and_get_contract(symbol=symbol)
        origin_margin_type = contract.margin_type
        origin_leverage = contract.current_leverage
        new_margin_type = trading_enums.MarginType.CROSS \
            if origin_margin_type is trading_enums.MarginType.ISOLATED else trading_enums.MarginType.ISOLATED
        if not self.exchange_manager.exchange.SUPPORTS_SET_MARGIN_TYPE:
            assert origin_margin_type in (trading_enums.MarginType.ISOLATED, trading_enums.MarginType.CROSS)
            with pytest.raises(trading_errors.NotSupported):
                await self.exchange_manager.exchange.connector.set_symbol_margin_type(symbol, True)
            with pytest.raises(trading_errors.NotSupported):
                await self.set_margin_type(new_margin_type, symbol=symbol)
            return
        if not has_open_position or (
            has_open_position and self.exchange_manager.exchange.SUPPORTS_SET_MARGIN_TYPE_ON_OPEN_POSITIONS
        ):
            await self.set_margin_type(new_margin_type, symbol=symbol)
            position = await self.get_position(symbol=symbol)
            if allow_empty_position and (
                position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value] != trading_constants.ZERO
                or self.SUPPORTS_EMPTY_POSITION_SET_MARGIN_TYPE
            ):
                # did not change leverage
                await self._check_margin_type_and_leverage(new_margin_type, origin_leverage, symbol=symbol)
            # restore margin type
            await self.set_margin_type(origin_margin_type, symbol=symbol)
        else:
            # has_open_position and not self.exchange_manager.exchange.SUPPORTS_SET_MARGIN_TYPE_ON_OPEN_POSITIONS
            with pytest.raises(trading_errors.NotSupported):
                await self.set_margin_type(new_margin_type, symbol=symbol)
        # did not change leverage
        await self._check_margin_type_and_leverage(origin_margin_type, origin_leverage, symbol=symbol)

    async def set_margin_type(self, margin_type, symbol=None):
        await self.exchange_manager.exchange.set_symbol_margin_type(
            symbol or self.SYMBOL,
            margin_type is trading_enums.MarginType.ISOLATED
        )

    async def _check_margin_type_and_leverage(self, expected_margin_type, expected_leverage, symbol=None):
        margin_type, leverage = await self.get_margin_type_and_leverage_from_position(symbol=symbol)
        assert expected_margin_type is margin_type
        assert expected_leverage == leverage
        if self.SUPPORTS_GET_LEVERAGE:
            assert expected_leverage == await self.get_leverage(symbol=symbol)

    def _check_positions_content(self, positions):
        for position in positions:
            self._check_position_content(position, None)

    def _check_position_content(self, position, symbol, position_mode=None, margin_type=None):
        if symbol:
            assert position[trading_enums.ExchangeConstantsPositionColumns.SYMBOL.value] == symbol
        else:
            assert position[trading_enums.ExchangeConstantsPositionColumns.SYMBOL.value]
        if margin_type:
            assert position[trading_enums.ExchangeConstantsPositionColumns.MARGIN_TYPE.value] == margin_type
        leverage = position[trading_enums.ExchangeConstantsPositionColumns.LEVERAGE.value]
        assert isinstance(leverage, decimal.Decimal)
        # should not be 0 in octobot
        assert leverage > 0
        assert position[trading_enums.ExchangeConstantsPositionColumns.MARGIN_TYPE.value] is not None
        assert position[trading_enums.ExchangeConstantsPositionColumns.POSITION_MODE.value] is not None
        if position_mode is not None:
            assert position[trading_enums.ExchangeConstantsPositionColumns.POSITION_MODE.value] is position_mode

    async def inner_test_create_and_cancel_limit_orders(self, symbol=None, settlement_currency=None):
        if self.exchange_manager.exchange.SUPPORTS_SET_MARGIN_TYPE:
            await self._inner_test_create_and_cancel_limit_orders_for_margin_type(
                symbol=symbol, settlement_currency=settlement_currency, margin_type=trading_enums.MarginType.ISOLATED
            )
            await self._inner_test_create_and_cancel_limit_orders_for_margin_type(
                symbol=symbol, settlement_currency=settlement_currency, margin_type=trading_enums.MarginType.CROSS
            )
        else:
            await self._inner_test_create_and_cancel_limit_orders_for_margin_type(
                symbol=symbol, settlement_currency=settlement_currency, margin_type=None
            )

    async def _inner_test_create_and_cancel_limit_orders_for_margin_type(
            self, symbol=None, settlement_currency=None, margin_type=None
    ):
        # test with linear symbol
        if margin_type is not None:
            await self.set_margin_type(margin_type)
        await super().inner_test_create_and_cancel_limit_orders(margin_type=margin_type)
        # test with inverse symbol
        if margin_type is not None:
            await self.set_margin_type(margin_type, symbol=self.INVERSE_SYMBOL)
        await super().inner_test_create_and_cancel_limit_orders(
            symbol=self.INVERSE_SYMBOL, settlement_currency=self.ORDER_CURRENCY, margin_type=margin_type
        )

    def _ensure_required_permissions(self, permissions):
        super()._ensure_required_permissions(permissions)
        assert trading_backend.enums.APIKeyRights.FUTURES_TRADING in permissions

    async def inner_test_create_and_fill_market_orders(self):
        portfolio = await self.get_portfolio()
        position = await self.get_position()
        pre_order_positions = await self.get_positions()
        current_price = await self.get_price()
        price = self.get_order_price(current_price, False)
        size = self.get_order_size(portfolio, price)
        # buy: increase position
        buy_market = await self.create_market_order(current_price, size, trading_enums.TradeOrderSide.BUY)
        self.check_created_market_order(buy_market, size, trading_enums.TradeOrderSide.BUY)
        post_buy_portfolio = {}
        post_buy_position = None
        try:
            await self.wait_for_fill(buy_market)
            post_buy_portfolio = await self.get_portfolio()
            post_buy_position = await self.get_position()
            self._check_position_content(post_buy_position, self.SYMBOL,
                                         position_mode=trading_enums.PositionMode.ONE_WAY)
            self.check_portfolio_changed(portfolio, post_buy_portfolio, False)
            self.check_position_changed(position, post_buy_position, True)
            post_order_positions = await self.get_positions()
            self.check_position_in_positions(pre_order_positions + post_order_positions)
            # now that position is open, test margin type update
            await self.inner_test_get_and_set_margin_type(has_open_position=True)

        finally:
            # sell: reset portfolio & position
            sell_market = await self.create_market_order(current_price, size, trading_enums.TradeOrderSide.SELL)
            self.check_created_market_order(sell_market, size, trading_enums.TradeOrderSide.SELL)
            await self.wait_for_fill(sell_market)
            post_sell_portfolio = await self.get_portfolio()
            post_sell_position = await self.get_position()
            if post_buy_portfolio:
                self.check_portfolio_changed(post_buy_portfolio, post_sell_portfolio, True)
            if post_buy_position is not None:
                self.check_position_changed(post_buy_position, post_sell_position, False)
            # position is back to what it was at the beginning on the test
            self.check_position_size(position, post_sell_position)

    def get_order_size(self, portfolio, price, symbol=None, order_size=None, settlement_currency=None):
        symbol = symbol or self.SYMBOL
        size = super().get_order_size(
            portfolio, price, symbol=symbol, order_size=order_size, settlement_currency=settlement_currency
        )
        # size in contracts: offset to closest contract
        contract_size = self.exchange_manager.exchange.connector.get_contract_size(symbol)
        if contract_size > 1:
            return size - size % contract_size
        return size

    async def get_position(self, symbol=None):
        return await self.exchange_manager.exchange.get_position(symbol or self.SYMBOL)

    async def get_positions(self, symbols=None):
        symbols = symbols or None
        if symbols is None and self.exchange_manager.exchange.REQUIRES_SYMBOL_FOR_EMPTY_POSITION:
            if self.INVERSE_SYMBOL is None:
                raise AssertionError(f"INVERSE_SYMBOL is required")
            symbols = [self.SYMBOL, self.INVERSE_SYMBOL]
        return await self.exchange_manager.exchange.get_positions(symbols=symbols)

    async def init_and_get_contract(self, symbol=None):
        symbol = symbol or self.SYMBOL
        await self.exchange_manager.exchange.load_pair_future_contract(symbol)
        if not self.exchange_manager.exchange.has_pair_future_contract(symbol):
            raise AssertionError(f"{symbol} contract not initialized")
        return self.exchange_manager.exchange.get_pair_future_contract(symbol)

    async def get_margin_type_and_leverage_from_position(self, symbol=None):
        position = await self.get_position(symbol=symbol)
        return (
            position[trading_enums.ExchangeConstantsPositionColumns.MARGIN_TYPE.value],
            position[trading_enums.ExchangeConstantsPositionColumns.LEVERAGE.value],
        )

    async def get_leverage(self, symbol=None):
        leverage = await self.exchange_manager.exchange.get_symbol_leverage(symbol or self.SYMBOL)
        return leverage[trading_enums.ExchangeConstantsLeveragePropertyColumns.LEVERAGE.value]

    async def set_leverage(self, leverage, symbol=None):
        return await self.exchange_manager.exchange.set_symbol_leverage(symbol or self.SYMBOL, float(leverage))

    @contextlib.asynccontextmanager
    async def required_empty_position(self):
        position = await self.get_position()
        if not self.is_position_empty(position):
            raise AssertionError(f"Empty {self.SYMBOL} position required for bundle orders tests")
        try:
            yield
        finally:
            position = await self.get_position()
            assert self.is_position_empty(position)

    async def load_contract(self, symbol=None):
        symbol = symbol or self.SYMBOL
        if self.exchange_manager.is_future and symbol not in self.exchange_manager.exchange.pair_contracts:
            await self.exchange_manager.exchange.load_pair_future_contract(symbol)

    async def enable_partial_take_profits_and_stop_loss(self, mode, symbol=None):
        await self.exchange_manager.exchange.set_symbol_partial_take_profit_stop_loss(
            symbol or self.SYMBOL, False, trading_enums.TakeProfitStopLossMode.PARTIAL
        )

    async def create_market_stop_loss_order(self, current_price, stop_price, size, side, symbol=None,
                                            push_on_exchange=True):
        await self.enable_partial_take_profits_and_stop_loss(trading_enums.TakeProfitStopLossMode.PARTIAL,
                                                             symbol=symbol)
        return await super().create_market_stop_loss_order(
            current_price, stop_price, size, side, symbol=symbol,
            push_on_exchange=push_on_exchange
        )

    async def create_order(self, price, current_price, size, side, order_type,
                           symbol=None, push_on_exchange=True):
        if not size:
            raise AssertionError(f"Size in required to create an order, provided size is {size}")
        # contracts are required to create orders
        await self.load_contract(symbol)
        return await super().create_order(
            price, current_price, size, side, order_type,
            symbol=symbol, push_on_exchange=push_on_exchange
        )

    def check_position_changed(self, previous_position, updated_position, has_increased, symbol=None):
        # use unified enums as it the ccxt position should have been parsed already
        previous_size = previous_position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value]
        updated_size = updated_position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value]
        if has_increased:
            assert updated_size > previous_size
        else:
            assert updated_size < previous_size

    def check_position_size(self, previous_position, updated_position):
        assert previous_position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value] == \
               updated_position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value]

    def has_empty_position(self, positions):
        for position in positions:
            if self.is_position_empty(position):
                # empty positions included in get_positions
                return True
        return False

    def get_filtered_positions(self, positions, contract_type):
        return [
            position
            for position in positions
            if position[trading_enums.ExchangeConstantsPositionColumns.CONTRACT_TYPE.value] is contract_type
        ]

    def get_other_position_symbol(self, positions_blacklist, contract_type):
        ignored_symbols = set(
            position[trading_enums.ExchangeConstantsPositionColumns.SYMBOL.value]
            for position in positions_blacklist
        )
        for symbol in self.exchange_manager.exchange.connector.client.markets:
            if symbol in ignored_symbols or self.exchange_manager.exchange.is_expirable_symbol(symbol):
                continue
            if contract_type is trading_enums.FutureContractType.INVERSE_PERPETUAL \
               and self.exchange_manager.exchange.is_inverse_symbol(symbol):
                return symbol
            elif contract_type is trading_enums.FutureContractType.LINEAR_PERPETUAL \
                 and self.exchange_manager.exchange.is_linear_symbol(symbol):
                return symbol
        raise AssertionError(f"No free symbol for {contract_type}")

    def is_position_empty(self, position):
        if position is None:
            raise AssertionError(
                f"Fetched empty position should never be None as a symbol parameter is given"
            )
        return position[trading_enums.ExchangeConstantsPositionColumns.SIZE.value] == trading_constants.ZERO

    def check_position_in_positions(self, positions, symbol=None):
        symbol = symbol or self.SYMBOL
        for position in positions:
            if position[trading_enums.ExchangeConstantsPositionColumns.SYMBOL.value] == symbol:
                return True
        raise AssertionError(f"Can't find position for symbol: {symbol}")

    async def order_in_open_orders(self, previous_open_orders, order, symbol=None):
        open_orders = await self.get_open_orders(self.get_exchange_data(symbol=symbol))
        assert len(open_orders) == len(previous_open_orders) + 1
        for open_order in open_orders:
            if open_order[trading_enums.ExchangeConstantsOrderColumns.EXCHANGE_ID.value] == order.exchange_order_id:
                return True
        return False

    def _get_edit_order_settlement_currency(self):
        return self.SETTLEMENT_CURRENCY

    def check_theoretical_cost(self, symbol, quantity, price, cost):
        if symbol.is_inverse():
            theoretical_cost = quantity
        else:
            theoretical_cost = quantity * price
        assert theoretical_cost * decimal.Decimal("0.8") <= cost <= theoretical_cost * decimal.Decimal("1.2")

    def _get_all_symbols(self):
        return [
            symbol
            for symbol in (self.SYMBOL, self.INVERSE_SYMBOL)
            if symbol
        ]
