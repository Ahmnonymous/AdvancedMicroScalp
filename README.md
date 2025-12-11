# MT5 Trading Bot

High-frequency trading bot for MetaTrader 5 with comprehensive risk management, SL enforcement, and monitoring.

## Quick Start

```bash
# Run the bot
python launch_system.py

# Or run with monitoring
python run_bot_with_monitoring.py
```

## Project Structure

```
.
├── bot/                    # Main trading bot logic
├── execution/              # MT5 connection and order management
├── risk/                   # Risk management and SL enforcement
├── strategies/             # Trading strategies
├── monitor/                # Monitoring and reconciliation
├── tests/                   # Unit and integration tests
├── verification/            # Verification tests and reports
├── config.json             # Main configuration file
├── launch_system.py        # Main launcher
└── README.md               # This file
```

## Key Features

- **Stop-Loss Management:** Comprehensive SL calculation and enforcement
- **Risk Management:** Strict loss limits, break-even, trailing stops
- **Real-time Monitoring:** Live trade monitoring and reconciliation
- **Safety Mechanisms:** Rate limiting, symbol blocking, validation
- **Comprehensive Testing:** Full verification across all symbols

## Configuration

Edit `config.json` to configure:
- MT5 connection settings
- Risk parameters
- Trading strategies
- Monitoring intervals

## Testing

### Unit Tests
```bash
python -m pytest tests/
```

### Verification
```bash
# Discover symbols
python verification/discover_symbols.py

# Run comprehensive verification
python verification/tests/comprehensive_symbol_verification.py

# Generate report
python verification/generate_final_report.py
```

See `verification/README.md` for detailed verification instructions.

## Documentation

- **Main README:** This file
- **Verification:** `verification/README.md`
- **Test Documentation:** `tests/README_PHASE4.md`

## Important Notes

- Always test in simulation/paper trading mode before live trading
- Review verification reports before enabling symbols
- Monitor logs regularly for errors and warnings
- Follow gradual re-enablement plan for problematic symbols

## Status

✅ **Verification Complete:** 440 symbols tested, 83% pass rate  
✅ **Safety Mechanisms:** All active and verified  
✅ **Ready for:** Simulation testing
