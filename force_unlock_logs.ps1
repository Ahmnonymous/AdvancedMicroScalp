# Force Unlock and Delete Logs Folder (PowerShell)
# This script forcefully closes any processes that might be locking log files

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "FORCE UNLOCKING LOGS FOLDER" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Find processes using log files
Write-Host "`n[STEP 1] Checking for processes using log files..." -ForegroundColor Yellow

$logFiles = Get-ChildItem -Path "logs" -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -eq ".log" }

if ($logFiles) {
    Write-Host "Found $($logFiles.Count) log files" -ForegroundColor Green
    
    # Try to close files using handle.exe if available, or use PowerShell methods
    Write-Host "`n[STEP 2] Attempting to close file handles..." -ForegroundColor Yellow
    
    # Method 1: Use Python to close loggers
    Write-Host "  Running Python cleanup script..." -ForegroundColor Gray
    python unlock_logs_folder.py 2>&1 | Out-Null
    
    # Wait a bit
    Start-Sleep -Seconds 1
}

# Step 3: Try to delete logs folder
Write-Host "`n[STEP 3] Attempting to delete logs folder..." -ForegroundColor Yellow

if (Test-Path "logs") {
    try {
        # Try normal delete first
        Remove-Item -Path "logs" -Recurse -Force -ErrorAction Stop
        Write-Host "[SUCCESS] Logs folder deleted successfully!" -ForegroundColor Green
    }
    catch {
        Write-Host "[WARNING] Normal delete failed, trying alternative method..." -ForegroundColor Yellow
        
        # Alternative: Delete files one by one, then folder
        try {
            Get-ChildItem -Path "logs" -Recurse -File | ForEach-Object {
                try {
                    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
                } catch {
                    Write-Host "  Could not delete: $($_.FullName)" -ForegroundColor Red
                }
            }
            Start-Sleep -Seconds 0.5
            Remove-Item -Path "logs" -Recurse -Force -ErrorAction Stop
            Write-Host "[SUCCESS] Logs folder deleted using alternative method!" -ForegroundColor Green
        }
        catch {
            Write-Host "[ERROR] Still cannot delete logs folder" -ForegroundColor Red
            Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host "`n  SOLUTIONS:" -ForegroundColor Yellow
            Write-Host "  1. Close all Python processes:" -ForegroundColor White
            Write-Host "     Get-Process python* | Stop-Process -Force" -ForegroundColor Gray
            Write-Host "  2. Close your IDE/editor (VS Code, PyCharm, etc.)" -ForegroundColor White
            Write-Host "  3. Restart your computer if needed" -ForegroundColor White
            Write-Host "  4. Use Process Explorer to find which process has the file open" -ForegroundColor White
            exit 1
        }
    }
}
else {
    Write-Host "[INFO] Logs folder does not exist" -ForegroundColor Cyan
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "UNLOCK COMPLETE" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

