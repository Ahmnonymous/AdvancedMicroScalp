#!/usr/bin/env python3
"""
Test SL calculation fix for SELL orders
"""

# Test case from user's report:
# SELL order: Entry 50.6, SL 50.77, Current 50.65
# Expected: SL above entry = loss protection (negative P/L)
# Current P/L: -$0.05 (correct)
# Effective SL should be: negative (loss protection), not positive

entry_price = 50.6
sl_price = 50.77
lot_size = 0.01
contract_size = 1.0
order_type = 'SELL'

# Current (WRONG) calculation:
sl_distance_wrong = sl_price - entry_price  # 50.77 - 50.6 = 0.17
effective_sl_wrong = lot_size * sl_distance_wrong * contract_size  # 0.01 * 0.17 * 1 = 0.0017 (WRONG - positive!)

# Correct calculation:
# For SELL: SL above entry = loss
# Loss = (sl_price - entry_price) * lot * contract
# But we want negative value for loss protection
sl_distance_correct = entry_price - sl_price  # 50.6 - 50.77 = -0.17
effective_sl_correct = -lot_size * sl_distance_correct * contract_size  # -0.01 * (-0.17) * 1 = 0.0017 (still wrong!)

# Actually, let's think differently:
# For SELL: if SL is above entry, that's a loss
# Loss in price terms = sl_price - entry_price = 0.17
# Loss in P/L terms = -(sl_price - entry_price) * lot * contract = -0.17 * 0.01 * 1 = -0.0017
effective_sl_correct2 = -(sl_price - entry_price) * lot_size * contract_size
# = -(50.77 - 50.6) * 0.01 * 1 = -0.17 * 0.01 = -0.0017 (NEGATIVE = correct!)

print(f"Entry: {entry_price}, SL: {sl_price}")
print(f"Wrong calculation: {effective_sl_wrong} (positive - WRONG!)")
print(f"Correct calculation: {effective_sl_correct2} (negative - CORRECT!)")
print(f"\nFor SELL orders:")
print(f"  SL above entry = loss protection = negative P/L")
print(f"  Formula: -(sl_price - entry_price) * lot * contract")

