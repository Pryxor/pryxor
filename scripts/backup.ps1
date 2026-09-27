<#
.SYNOPSIS
    Pryxor — SQLite backup (Windows / PowerShell).

.DESCRIPTION
    Creates a consistent backup of the Pryxor state DB using VACUUM INTO.
    Works while Pryxor is running (WAL mode).

.PARAMETER StatePath
    Path to the state DB. Default: ./pryxor_state.sqlite3

.PARAMETER OutDir
    Backup output directory. Default: ./backups

.PARAMETER Keep
    Number of backups to retain. Default: 14

.EXAMPLE
    .\scripts\backup.ps1
    .\scripts\backup.ps1 -StatePath C:\data\pryxor_state.sqlite3 -OutDir D:\backups
#>

[CmdletBinding()]
param(
    [string]$StatePath = $env:PRYXOR_STATE_PATH,
    [string]$OutDir = $env:PRYXOR_BACKUP_DIR,
    [int]$Keep = 14
)

$ErrorActionPreference = "Stop"

if (-not $StatePath) { $StatePath = ".\pryxor_state.sqlite3" }
if (-not $OutDir) { $OutDir = ".\backups" }

# --- Vérifications ---------------------------------------------------
if (-not (Test-Path $StatePath)) {
    Write-Error "State DB not found: $StatePath"
    exit 1
}

$sqlite = Get-Command sqlite3 -ErrorAction SilentlyContinue
if (-not $sqlite) {
    Write-Error "sqlite3 CLI not found. Install it (choco install sqlite)."
    exit 1
}

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}

# --- Nom de fichier ---------------------------------------------------
$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$baseName = "pryxor_backup_$timestamp"
$backupFile = Join-Path $OutDir "$baseName.sqlite3"
$finalFile = "$backupFile.zip"

# --- VACUUM INTO -----------------------------------------------------
Write-Host "▶ Backing up $StatePath → $backupFile"

if (Test-Path $backupFile) { Remove-Item $backupFile -Force }

# Important : guillemets simples échappés pour SQLite
$sql = "VACUUM INTO '$($backupFile -replace "'", "''")';"
& sqlite3 $StatePath $sql

if (-not (Test-Path $backupFile)) {
    Write-Error "VACUUM INTO failed (no output file)."
    exit 1
}

$sizeMb = [math]::Round((Get-Item $backupFile).Length / 1MB, 2)
Write-Host "  ✅ Snapshot: ${sizeMb} MB"

# --- Compression -----------------------------------------------------
Write-Host "▶ Compressing..."
Compress-Archive -Path $backupFile -DestinationPath $finalFile -Force
Remove-Item $backupFile -Force

$finalMb = [math]::Round((Get-Item $finalFile).Length / 1MB, 2)
Write-Host "  ✅ Compressed: ${finalMb} MB"

# --- Rotation --------------------------------------------------------
if ($Keep -gt 0) {
    Write-Host "▶ Rotating (keeping last $Keep backups)"
    $backups = Get-ChildItem -Path $OutDir -Filter "pryxor_backup_*.sqlite3.zip" |
               Sort-Object LastWriteTime -Descending
    $backups | Select-Object -Skip $Keep | ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Host "  🗑  Removed: $($_.Name)"
    }
}

Write-Host ""
Write-Host "✅ Backup complete: $finalFile"
exit 0