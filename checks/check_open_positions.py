#!/usr/bin/env python3
"""Check current open positions"""

import os
import sys
import MetaTrader5 as mt5
import json
from datetime import datetime

# Add parent directory to path to access root config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Get root directory path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(root_dir, 'config.json')

with open(config_path, 'r') as f:
    config = json.load(f)

mt5.initialize()
mt5.login(
    login=int(config['mt5']['account']),
    password=config['mt5']['password'],
    server=config['mt5']['server']
)

positions = mt5.positions_get()

print("="*80)
print("OPEN POSITIONS")
print("="*80)
print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()

if positions is None or len(positions) == 0:
    print("No open positions")
else:
    print(f"Found {len(positions)} open position(s):\n")
    for pos in positions:
        time_open = datetime.fromtimestamp(pos.time)
        print(f"Ticket: {pos.ticket}")
        print(f"Symbol: {pos.symbol}")
        print(f"Type: {'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL'}")
        print(f"Volume: {pos.volume}")
        print(f"Entry Price: {pos.price_open:.5f}")
        print(f"Current Price: {pos.price_current:.5f}")
        print(f"Stop Loss: {pos.sl:.5f}")
        print(f"Take Profit: {pos.tp:.5f}")
        print(f"Profit: ${pos.profit:.2f}")
        print(f"Swap: ${pos.swap:.2f}")
        print(f"Time Open: {time_open.strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 80)

# Get closed positions (from history)
from_date = datetime.now().timestamp() - 3600  # Last hour
to_date = datetime.now().timestamp()
deals = mt5.history_deals_get(from_date, to_date)

if deals:
    print(f"\nRecent Closed Positions (last hour):")
    print("="*80)
    for deal in deals[:10]:
        if deal.entry == mt5.DEAL_ENTRY_OUT:
            time_deal = datetime.fromtimestamp(deal.time)
            print(f"Ticket: {deal.position_id} | Symbol: {deal.symbol} | Profit: ${deal.profit:.2f} | Time: {time_deal.strftime('%H:%M:%S')}")

mt5.shutdown()

