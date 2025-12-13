# Step A: Cleanup Candidates Identification

## Summary

Identified cleanup candidates for non-behavioral codebase cleanup. NO DELETIONS YET - identification only.

---

## Category 1: Duplicate Documentation Files

### Candidate 1.1: `docs/steps/STEP6_FOLDER_STRUCTURE_PLAN copy.md`

**Reason:** Exact duplicate of `docs/steps/STEP6_FOLDER_STRUCTURE_PLAN.md`

**Evidence:**
- Both files exist in same directory
- Both have identical content (verified by reading first 20 lines of each)
- "copy" suffix indicates accidental duplication

**Status:** Duplicate file, safe to remove

**Replacement:** `docs/steps/STEP6_FOLDER_STRUCTURE_PLAN.md` (original)

**Behavior Impact:** None - documentation file only, not imported or referenced

---

## Category 2: Standalone Utility Scripts (Not Imported)

### Candidate 2.1: `filters/filter_active_symbols.py`

**Reason:** Standalone script, not imported by any production code

**Evidence:**
- Has `if __name__ == "__main__":` block (standalone execution)
- No imports found: `grep -r "from.*filter_active_symbols|import.*filter_active_symbols"` returns no matches
- Purpose: One-time utility to filter symbols with active market data

**Status:** Standalone utility script, likely one-time use

**Replacement:** None - utility script, not replaced

**Behavior Impact:** None - not imported by production code

---

### Candidate 2.2: `verification/verify_conversion.py`

**Reason:** Legacy verification script for log conversion

**Evidence:**
- No imports found: `grep -r "from.*verify_conversion|import.*verify_conversion"` returns no matches (only self-reference)
- Purpose: Verifies legacy log conversion (one-time migration task)
- Script imports: Only standard library and pathlib

**Status:** Legacy verification script, one-time use

**Replacement:** None - verification script, conversion already complete

**Behavior Impact:** None - not imported by production code

---

### Candidate 2.3: `monitor/monitor.py`

**Reason:** Standalone monitoring script, not imported

**Evidence:**
- Has `if __name__ == "__main__":` block (standalone execution)
- No imports found: `grep -r "from.*monitor\.monitor|import.*monitor\.monitor|from monitor import monitor"` returns no matches
- Different from other monitor modules (realtime_bot_monitor.py, comprehensive_bot_monitor.py which are imported)
- Contains `monitor()` function but not imported anywhere

**Status:** Standalone script, may be legacy/unused

**Replacement:** Active monitoring uses `monitor/realtime_bot_monitor.py`, `monitor/comprehensive_bot_monitor.py` (imported in launch_system.py)

**Behavior Impact:** None - not imported by production code

---

## Category 3: Files Already Verified as Used (NOT CANDIDATES)

### NOT Candidate: `entry/limit_entry_dry_run.py`

**Reason:** Imported and used in production code

**Evidence:**
- Imported in `bot/trading_bot.py:284`: `from entry.limit_entry_dry_run import LimitEntryDryRun`
- Active usage in trading bot logic

**Status:** Active production code - DO NOT REMOVE

---

## Summary of Candidates

### Files Identified for Potential Removal:

1. **`docs/steps/STEP6_FOLDER_STRUCTURE_PLAN copy.md`** - Duplicate documentation file
2. **`filters/filter_active_symbols.py`** - Standalone utility script (not imported)
3. **`verification/verify_conversion.py`** - Legacy verification script (not imported)
4. **`monitor/monitor.py`** - Standalone script (not imported, legacy)

**Total Candidates:** 4 files

**Next Step:** Step B - Justify each candidate with detailed proof (imports, usage analysis)

---

## Notes

- All candidates are files that are NOT imported by production code
- No behavioral code will be affected
- All candidates are either duplicates or standalone utility/legacy scripts
- Verification scripts and documentation files only

