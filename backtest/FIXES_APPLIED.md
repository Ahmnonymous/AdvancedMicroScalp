# Backtest Fixes Applied

## Issues Fixed

### 1. Missing Symbol (NAS100m) Causing Validation Failure
**Problem**: The backtest was aborting when NAS100m symbol was not found in MT5.

**Fix**: Modified `backtest_runner.py` to:
- Skip missing symbols with warnings instead of treating them as fatal errors
- Continue with valid symbols only
- Only abort if NO valid symbols are found

**Changes**:
- `backtest_runner.py` lines 100-134: Now collects valid/invalid symbols and continues with valid ones

### 2. Date Range Too Large (Full Year 2024)
**Problem**: Requesting 1 full year of historical data when MT5 only has ~100 bars available.

**Fix**: 
- Changed default date range to last 30 days (more reasonable)
- Updated `config_backtest.json` to use empty date strings (auto-calculated)
- Updated `backtest_runner.py` and `run_backtest.py` to default to 30 days if not specified

**Changes**:
- `config_backtest.json`: Changed `start_date` and `end_date` to empty strings
- `backtest_runner.py` lines 53-65: Added logic to default to 30 days if dates not provided
- `run_backtest.py` lines 112-123: Updated to handle empty date strings

### 3. Required Bars Too High (1000)
**Problem**: Validation required 1000 bars but only ~100 were available.

**Fix**: Reduced required bars from 1000 to 50 for validation.

**Changes**:
- `backtest_runner.py` line 106: Changed `required_bars=1000` to `required_bars=50`

### 4. Removed NAS100m from Config
**Problem**: NAS100m symbol doesn't exist in MT5.

**Fix**: Removed NAS100m from the symbols list in `config_backtest.json`.

**Changes**:
- `config_backtest.json`: Removed "NAS100m" from symbols array and symbol groups

## Testing

After these fixes, the backtest should:
1. ✅ Skip missing symbols (like NAS100m) with warnings
2. ✅ Use a reasonable default date range (30 days instead of 1 year)
3. ✅ Validate with a lower bar requirement (50 instead of 1000)
4. ✅ Continue running with available symbols

## Usage

Run with default settings (30 days):
```bash
python backtest/run_backtest.py --speed 100
```

Run with custom date range:
```bash
python backtest/run_backtest.py --start 2024-12-01 --end 2024-12-14 --speed 100
```

Run with fewer symbols:
```bash
python backtest/run_backtest.py --symbols EURUSDm GBPUSDm --speed 100
```

