#!/usr/bin/env python3
"""Check trading permissions and symbol tradeability."""

import MetaTrader5 as mt5
import json

with open('config.json', 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

account = mt5.account_info()
if account:
    print("\nAccount Trading Permissions:")
    print("=" * 60)
    print(f"Trade Allowed: {account.trade_allowed}")
    print(f"Trade Expert: {account.trade_expert}")
    print(f"Trade Mode: {account.trade_mode}")
    print(f"Margin Mode: {account.margin_mode}")
    print(f"Margin So Mode: {account.margin_so_mode}")

# Check symbol tradeability
symbol = 'XRPUSDm'
symbol_info = mt5.symbol_info(symbol)
if symbol_info:
    print(f"\n{symbol} Tradeability:")
    print("=" * 60)
    print(f"Trade Mode: {symbol_info.trade_mode}")
    print(f"  {mt5.SYMBOL_TRADE_MODE_DISABLED} = Disabled")
    print(f"  {mt5.SYMBOL_TRADE_MODE_LONGONLY} = Long only")
    print(f"  {mt5.SYMBOL_TRADE_MODE_SHORTONLY} = Short only")
    print(f"  {mt5.SYMBOL_TRADE_MODE_CLOSEONLY} = Close only")
    print(f"  {mt5.SYMBOL_TRADE_MODE_FULL} = Full trading")
    print(f"Trade Stops Level: {symbol_info.trade_stops_level}")
    print(f"Trade Freeze Level: {symbol_info.trade_freeze_level}")
    
    # Try to get current price
    tick = mt5.symbol_info_tick(symbol)
    if tick:
        print(f"Current Bid: {tick.bid}")
        print(f"Current Ask: {tick.ask}")
        print(f"Last: {tick.last}")
    else:
        print("Cannot get tick data")

mt5.shutdown()

