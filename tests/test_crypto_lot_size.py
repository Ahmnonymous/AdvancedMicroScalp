#!/usr/bin/env python3
"""Test lot size calculation for crypto symbols."""

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

symbol = 'XRPUSDm'
symbol_info = mt5.symbol_info(symbol)

if symbol_info:
    print(f"\n{symbol} Symbol Info:")
    print(f"  Bid: {symbol_info.bid}")
    print(f"  Ask: {symbol_info.ask}")
    print(f"  Point: {symbol_info.point}")
    print(f"  Digits: {symbol_info.digits}")
    print(f"  Contract Size: {symbol_info.trade_contract_size}")
    print(f"  Volume Min: {symbol_info.volume_min}")
    print(f"  Volume Max: {symbol_info.volume_max}")
    print(f"  Volume Step: {symbol_info.volume_step}")
    
    # Calculate pip value
    pip_value = symbol_info.point * 10 if symbol_info.digits == 5 or symbol_info.digits == 3 else symbol_info.point
    print(f"  Pip Value: {pip_value}")
    
    # Test lot size calculation for $2 risk, 20 pips SL
    risk_usd = 2.0
    stop_loss_pips = 20
    stop_loss_price = stop_loss_pips * pip_value
    contract_size = symbol_info.trade_contract_size
    
    print(f"\nLot Size Calculation:")
    print(f"  Risk: ${risk_usd}")
    print(f"  Stop Loss: {stop_loss_pips} pips = {stop_loss_price} price units")
    print(f"  Contract Size: {contract_size}")
    
    lot_size = risk_usd / (stop_loss_price * contract_size)
    print(f"  Calculated Lot Size: {lot_size}")
    print(f"  Rounded: {round(lot_size, 2)}")
    print(f"  With Volume Step ({symbol_info.volume_step}): {round(lot_size / symbol_info.volume_step) * symbol_info.volume_step}")

mt5.shutdown()

