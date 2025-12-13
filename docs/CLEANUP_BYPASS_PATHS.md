# Intentional Bypass Paths Documentation

This document describes intentional code paths that bypass standard SL/profit locking mechanisms for specific compliance or profit-taking purposes.

## 1. Halal Compliance Closures

**Location:** `risk/halal_compliance.py:142-159`

**Purpose:** Ensures compliance with Islamic/Halal trading principles by closing positions that violate overnight hold rules.

**Bypass Details:**
- **What:** Directly closes positions via `order_manager.close_position()` when they violate halal compliance rules
- **When:** Positions held overnight or exceeding max hold hours
- **Why:** Compliance requirement - must close immediately when rule violated
- **Safety:** 
  - Only closes when compliance rule is violated
  - Properly logged with `[HALAL]` prefix
  - Does not interfere with profit locking (closes before profit lock would apply)

**Code Reference:**
```python
def check_all_positions(self) -> None:
    """Check all open positions for halal compliance and close if needed."""
    if not self.enabled:
        return
    
    positions = self.order_manager.get_open_positions()
    
    for position in positions:
        # Check overnight hold
        if not self.check_overnight_hold(position):
            logger.warning(f"Closing position {position.get('ticket')} due to halal compliance violation")
            self.order_manager.close_position(
                position.get('ticket'),
                comment="Closed: Halal compliance - overnight hold"
            )
```

---

## 2. Micro Profit Engine Closures

**Location:** `bot/micro_profit_engine.py:411`

**Purpose:** Closes positions immediately when profit is in sweet spot range ($0.03-$0.10) to capture micro-profits.

**Bypass Details:**
- **What:** Directly closes positions via `order_manager.close_position()` when profit is in sweet spot
- **When:** Profit is between $0.03 and $0.10 (sweet spot range)
- **Why:** Profit-taking strategy - capture small profits quickly
- **Safety:**
  - Multiple checkpoints ensure never closes losing trades
  - Verifies SL is applied before closing (line 124-128)
  - Checks effective SL to ensure not closing at loss (line 130-145)
  - Only closes if profit >= $0.05 buffer (accounts for spread/slippage)
  - Never closes if profit < $0.03 or at stop-loss (-$2.00)

**Code Reference:**
```python
def check_and_close(self, position: Dict[str, Any], mt5_connector) -> bool:
    """
    Check if position should be closed and close it if profit is in sweet spot range.
    
    CRITICAL SAFETY: Multiple validation checkpoints ensure no negative-profit closures.
    """
    # ... validation checks ...
    
    # Verify SL is applied and verified before closing
    if hasattr(self, 'risk_manager') and self.risk_manager:
        if hasattr(self.risk_manager, '_profit_locking_engine'):
            profit_locking_engine = self.risk_manager._profit_locking_engine
            if self.min_profit_threshold_usd <= current_profit <= self.max_profit_threshold_usd:
                if hasattr(profit_locking_engine, 'is_sl_verified'):
                    is_verified = profit_locking_engine.is_sl_verified(ticket)
                    if not is_verified:
                        return False  # Wait for SL verification
    
    # ... additional safety checks ...
    
    close_success = self.order_manager.close_position(
        ticket=ticket,
        comment=f"Micro-HFT sweet spot profit (${final_pre_close_profit:.2f})"
    )
```

---

## Summary

Both bypass paths are:
- ✅ **Intentional** - Designed for specific purposes (compliance, profit-taking)
- ✅ **Safe** - Multiple validation checkpoints prevent unintended closures
- ✅ **Logged** - All closures are properly logged with descriptive comments
- ✅ **Documented** - This document serves as official documentation

**No other bypass paths exist.** All other position closures go through standard SL/profit locking mechanisms.

---

**Last Updated:** Step 1 Cleanup - 2024
**Status:** ✅ Documented and Verified

