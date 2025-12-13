#!/usr/bin/env python3
"""
Unlock and Delete Logs Folder
Closes all open log file handles and deletes the logs folder.
"""

import sys
import os
import time
import shutil

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.logger_factory import close_all_loggers
import logging

# Don't create a logger for this script - it might lock files
# Use print statements instead

def unlock_and_delete_logs():
    """Close all loggers and delete the logs folder."""
    
    print("=" * 80)
    print("UNLOCKING LOGS FOLDER")
    print("=" * 80)
    
    # Step 1: Close all loggers (multiple times to ensure cleanup)
    print("\n[STEP 1] Closing all log file handles...")
    for attempt in range(3):
        try:
            close_all_loggers()
            print(f"[SUCCESS] Attempt {attempt + 1}: All loggers closed")
        except Exception as e:
            print(f"[WARNING] Attempt {attempt + 1}: Error closing loggers: {e}")
        time.sleep(0.2)
    
    # Step 2: Close root logger handlers
    print("\n[STEP 2] Closing root logger handlers...")
    try:
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            try:
                handler.close()
                root_logger.removeHandler(handler)
            except Exception:
                pass
        print("[SUCCESS] Root logger handlers closed")
    except Exception as e:
        print(f"[WARNING] Error closing root logger: {e}")
    
    # Step 2.5: Force close all Python loggers
    print("\n[STEP 2.5] Force closing all Python loggers...")
    try:
        # Get all loggers
        for name in logging.Logger.manager.loggerDict:
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                try:
                    handler.close()
                    logger.removeHandler(handler)
                except Exception:
                    pass
        print("[SUCCESS] All Python loggers force-closed")
    except Exception as e:
        print(f"[WARNING] Error force-closing loggers: {e}")
    
    # Step 3: Wait for OS to release file handles
    print("\n[STEP 3] Waiting for OS to release file handles...")
    time.sleep(1.0)  # Longer wait
    print("[SUCCESS] Wait complete")
    
    # Step 4: Delete logs folder
    logs_path = "logs"
    if os.path.exists(logs_path):
        print(f"\n[STEP 4] Deleting logs folder: {logs_path}")
        try:
            shutil.rmtree(logs_path)
            print(f"[SUCCESS] Logs folder deleted successfully")
        except PermissionError as e:
            print(f"[ERROR] Permission denied - file may still be locked")
            print(f"   Error: {e}")
            print(f"\n   Try:")
            print(f"   1. Close any programs that might be using log files")
            print(f"   2. Check if backtest is still running")
            print(f"   3. Restart Python/IDE if needed")
            return False
        except Exception as e:
            print(f"[ERROR] Failed to delete logs folder: {e}")
            return False
    else:
        print(f"\n[INFO] Logs folder does not exist: {logs_path}")
    
    print("\n" + "=" * 80)
    print("UNLOCK COMPLETE")
    print("=" * 80)
    return True

if __name__ == "__main__":
    try:
        success = unlock_and_delete_logs()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n[INTERRUPTED] Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[FATAL ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

