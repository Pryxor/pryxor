<#
.SYNOPSIS
    Pryxor — SQLite restore (Windows / PowerShell).

.DESCRIPTION
    Restores a backup made by backup.ps1 or backup.sh.
    Pryxor MUST be stopped before running.

.EXAMPLE
    .\scripts\restore.ps1 -BackupFile .\backups\pryxor_backup_20260918T120000Z.sqlite3.zip
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [string]$BackupFile,

    [string]$StatePath = $env:PRYXOR_STATE_PATH
)

$ErrorActionPreference = "Stop"

if (-not $StatePath) { $StatePath = ".\pryxor_state.sqlite3" }

# --- Vérifications ---------------------------------------------------
if (-not (Test-Path $BackupFile)) {
    Write-Error "Backup file not found: $BackupFile"
    exit 1
}

$sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
if (-not $sqlite) {
    Write-Error "sqlite3 CLI not found."
    exit 1
}

# --- Confirmation ----------------------------------------------------
Write-Host "⚠️  This will REPLACE $StatePath with:" -ForegroundColor Yellow
Write-Host "     $BackupFile"
Write-Host ""
$confirm = Read-Host "Type 'yes' to continue"
if ($confirm -ne "yes") {
    Write-Host "Aborted."
    exit 0
}

# --- Décompression ---------------------------------------------------
$tmpDir = New-Item -ItemType Directory -Path "$env:TEMP\pryxor_restore_$(Get-Random)" -Force
try {
    Write-Host "▶ Decompressing..."
    Expand-Archive -Path $BackupFile -DestinationPath $tmpDir -Force
    $extracted = Get-ChildItem -Path $tmpDir -Filter "*.sqlite3" | Select-Object -First 1
    if (-not $extracted) {
        Write-Error "No .sqlite3 file found inside archive."
        exit 1
    }
    $workFile = $extracted.FullName

    # --- Validation --------------------------------------------------
    Write-Host "▶ Validating..."
    $integrity = & sqlite3 $workFile "PRAGMA integrity_check;"
    if ($integrity -ne "ok") {
        Write-Error "Integrity check failed: $integrity"
        exit 1
    }

    $tables = & sqlite3 $workFile "SELECT name FROM sqlite_master WHERE type='table';"
    foreach ($required in @("holds", "audit_events", "outbox_events")) {
        if ($tables -notcontains $required) {
            Write-Error "Backup is missing required table: $required"
            exit 1
        }
    }

    # --- Sauvegarde de l'ancienne DB ---------------------------------
    if (Test-Path $StatePath) {
        $old = "$StatePath.before_restore_$(Get-Date -Format 'yyyyMMddTHHmmssZ')"
        Write-Host "▶ Saving current state to: $old"
        Move-Item $StatePath $old
        Remove-Item "$StatePath-wal", "$StatePath-shm" -ErrorAction SilentlyContinue
    }

    # --- Restauration ------------------------------------------------
    Write-Host "▶ Restoring..."
    Copy-Item $workFile $StatePath

    Write-Host ""
    Write-Host "✅ Restore complete: $StatePath"
    Write-Host "   You can now start Pryxor."
}
finally {
    Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}