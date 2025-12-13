# Step 6 — Folder Structure & Organization Plan

## Current State Analysis

### Root Directory Issues:
1. **Documentation files scattered in root** (18 *.md files)
2. **Utility scripts in root** (`unlock_logs_folder.py`, `force_unlock_logs.ps1`)
3. **Runner scripts in root** (`run_parallel_system.py`)
4. **Empty directories** (`find/`, `verify/`, `docs/`)

### Current Structure Assessment:

**✅ Well Organized:**
- `bot/` - Core bot logic ✅
- `risk/` - Risk management ✅
- `execution/` - Execution layer ✅
- `strategies/` - Trading strategies ✅
- `filters/` - Market filters ✅
- `backtest/` - Backtest modules ✅
- `monitor/` - Monitoring modules ✅
- `utils/` - Utilities ✅
- `tests/` - Test files ✅
- `tools/analysis/` - Analysis tools ✅
- `verification/` - Verification scripts ✅
- `checks/` - Diagnostic checks ✅
- `scripts/` - Runner scripts ✅
- `config/` - Configuration files ✅
- `trade_logging/` - Trade logging ✅
- `news_filter/` - News filtering ✅
- `entry/` - Entry logic ✅

**⚠️ Needs Organization:**
- Root directory has too many files
- Documentation scattered
- Some utilities not in proper locations

---

## Reorganization Plan

### Phase 1: Clean Up Empty Directories

1. **Remove empty `find/` directory**
   - Contains only `__pycache__/`
   - No code references it
   - Action: Delete directory

2. **Remove empty `verify/` directory**
   - Contains only `__pycache__/`
   - No code references it (verification scripts are in `verification/`)
   - Action: Delete directory

3. **Keep `docs/` directory for documentation**

---

### Phase 2: Move Documentation Files

**Destination:** `docs/`

Move all verification/step documentation to `docs/steps/`:
- `STEP1_CLEANUP_REPORT.md`
- `STEP1_CLEANUP_COMPLETE.md`
- `STEP2A_VERIFICATION.md`
- `STEP2B_VERIFICATION.md`
- `STEP2C_VERIFICATION.md`
- `STEP2D_VERIFICATION.md`
- `STEP2E_VERIFICATION.md`
- `STEP2F_VERIFICATION.md`
- `POST_STEP2_VERIFICATION.md`
- `STEP3_VERIFICATION.md`
- `STEP4_VERIFICATION.md`
- `STEP5_VERIFICATION.md`
- `STEP5_LOGGING_CLEANUP_REPORT.md`
- `STEP5_LOGGING_CLEANUP_COMPLETE.md`
- `STEP8_VERIFICATION.md` (if exists)

Move other documentation:
- `CLEANUP_BYPASS_PATHS.md` → `docs/CLEANUP_BYPASS_PATHS.md`

**Keep in root:**
- `README.md` - Main project documentation

---

### Phase 3: Move Utility Scripts

**Destination:** `scripts/` or `utils/`

1. **`unlock_logs_folder.py`** → `scripts/unlock_logs_folder.py`
   - Utility script, belongs with other scripts
   - Action: Move to `scripts/`

2. **`force_unlock_logs.ps1`** → `scripts/force_unlock_logs.ps1`
   - PowerShell utility script
   - Action: Move to `scripts/`

3. **`run_parallel_system.py`** → `scripts/run_parallel_system.py`
   - System runner script
   - Action: Move to `scripts/`

---

### Phase 4: Verify Imports Still Work

After moving files, verify:
- All imports still resolve correctly
- Script paths are updated if needed
- No broken references

---

## Proposed Final Structure

```
TRADING/
├── bot/                    # Core bot logic
├── risk/                   # Risk management
├── execution/              # Execution layer
├── strategies/             # Trading strategies
├── filters/                # Market filters
├── backtest/               # Backtest modules
├── monitor/                # Monitoring modules
├── utils/                  # Utilities
├── tests/                  # Test files
├── tools/                  # Analysis tools
│   └── analysis/
├── verification/           # Verification scripts
├── checks/                 # Diagnostic checks
├── scripts/                # Runner/utility scripts
│   ├── run_bot.py
│   ├── run_bot_manual.py
│   ├── run_bot_with_monitoring.py
│   ├── unlock_logs_folder.py        # MOVED
│   ├── force_unlock_logs.ps1        # MOVED
│   └── run_parallel_system.py       # MOVED
├── config/                 # Configuration files
├── trade_logging/          # Trade logging
├── news_filter/            # News filtering
├── entry/                  # Entry logic
├── docs/                   # Documentation
│   ├── steps/              # Step verification docs
│   │   ├── STEP1_CLEANUP_REPORT.md
│   │   ├── STEP1_CLEANUP_COMPLETE.md
│   │   ├── STEP2A_VERIFICATION.md
│   │   ├── STEP2B_VERIFICATION.md
│   │   ├── STEP2C_VERIFICATION.md
│   │   ├── STEP2D_VERIFICATION.md
│   │   ├── STEP2E_VERIFICATION.md
│   │   ├── STEP2F_VERIFICATION.md
│   │   ├── POST_STEP2_VERIFICATION.md
│   │   ├── STEP3_VERIFICATION.md
│   │   ├── STEP4_VERIFICATION.md
│   │   ├── STEP5_VERIFICATION.md
│   │   ├── STEP5_LOGGING_CLEANUP_REPORT.md
│   │   ├── STEP5_LOGGING_CLEANUP_COMPLETE.md
│   │   └── STEP8_VERIFICATION.md
│   └── CLEANUP_BYPASS_PATHS.md
├── config.json             # Main config (stays in root)
├── launch_system.py        # Main launcher (stays in root)
└── README.md               # Main README (stays in root)
```

---

## Actions Summary

### Files to Move:

**Documentation (→ docs/steps/):**
- 13 STEP*.md files
- 1 CLEANUP*.md file

**Scripts (→ scripts/):**
- `unlock_logs_folder.py`
- `force_unlock_logs.ps1`
- `run_parallel_system.py`

### Directories to Remove:
- `find/` (empty)
- `verify/` (empty)

### Directories to Create:
- `docs/steps/` (for step documentation)

---

## Verification Checklist

After reorganization:
- [ ] All imports still work
- [ ] All scripts run correctly
- [ ] No broken file references
- [ ] Compilation succeeds
- [ ] Documentation accessible
- [ ] Root directory cleaner

---

## Notes

- Keep `config.json` and `launch_system.py` in root (standard practice)
- Keep `README.md` in root (standard practice)
- All other files should be in appropriate subdirectories
- Empty directories removed to reduce clutter

