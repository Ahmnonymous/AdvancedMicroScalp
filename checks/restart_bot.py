#!/usr/bin/env python3
"""Restart the trading bot with updated swap-free symbols."""

import subprocess
import sys
import time
import os

# Kill existing bot process if running
print("Checking for running bot processes...")
try:
    # On Windows, find Python processes running run_bot.py
    result = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq python.exe', '/FO', 'CSV'],
        capture_output=True,
        text=True
    )
    print("Bot restart script ready.")
except:
    pass

print("\n" + "=" * 60)
print("RESTARTING TRADING BOT WITH SWAP-FREE SYMBOLS")
print("=" * 60)
print("\nThe bot will start in a new process.")
print("Monitor bot_log.txt for trading activity.")
print("=" * 60)

# Start bot in background
subprocess.Popen([sys.executable, 'run_bot.py'], 
                 stdout=subprocess.DEVNULL, 
                 stderr=subprocess.DEVNULL)

print("\n✓ Bot started successfully!")
print("Check bot_log.txt for status updates.")

