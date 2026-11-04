<#
.SYNOPSIS
    Copy the server's new database dumps to this PC and keep the newest 30.

.DESCRIPTION
    scripts/backup-db.sh writes a dump of production's database to ~/blab-backups on the
    server every night. This copies, with scp and your SSH key, the dumps not yet in
    -Destination, then removes all but the newest -Keep there. If a copy fails it exits
    non-zero and removes nothing. Each run is logged to pull-backups.log in -Destination.
    Register it once with Task Scheduler: see the README, "Deploy".

.EXAMPLE
    .\scripts\pull-backups.ps1
    .\scripts\pull-backups.ps1 -Destination D:\BLab-backups -Keep 60
#>
param(
    [string]$SshTarget = "titouan@10.69.144.180",
    [string]$RemoteDir = "blab-backups",   # relative to the SSH user's home
    [string]$Destination = (Join-Path $env:USERPROFILE "BLab-backups"),
    [ValidateRange(1, 10000)][int]$Keep = 30
)

$ErrorActionPreference = "Stop"
$dumpName = '^blab-\d{8}T\d{6}Z\.dump$'   # what backup-db.sh writes
New-Item -ItemType Directory -Force $Destination | Out-Null
$logFile = Join-Path $Destination "pull-backups.log"

function Write-Log([string]$message) {
    $line = "$(Get-Date -Format s) $message"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

try {
    # BatchMode: a scheduled run must fail rather than wait for a password prompt
    $remote = ssh -o BatchMode=yes $SshTarget "ls -1 $RemoteDir"
    if ($LASTEXITCODE -ne 0) { throw "listing ${SshTarget}:$RemoteDir failed (exit code $LASTEXITCODE)" }
    $missing = @($remote | Where-Object { $_ -match $dumpName } |
        Where-Object { -not (Test-Path (Join-Path $Destination $_)) } | Sort-Object)

    foreach ($name in $missing) {
        # Copy under a temporary name, so an interrupted copy never counts as a dump
        $partial = Join-Path $Destination ".$name.partial"
        scp -q -o BatchMode=yes "${SshTarget}:$RemoteDir/$name" $partial
        if ($LASTEXITCODE -ne 0) {
            Remove-Item -Force -ErrorAction SilentlyContinue $partial
            throw "copying $name failed (exit code $LASTEXITCODE)"
        }
        Move-Item -Force $partial (Join-Path $Destination $name)
        Write-Log "copied $name"
    }

    # Names sort by time; remove all but the newest $Keep
    $local = @(Get-ChildItem -File $Destination | Where-Object { $_.Name -match $dumpName } | Sort-Object Name)
    if ($local.Count -gt $Keep) {
        foreach ($old in $local[0..($local.Count - $Keep - 1)]) {
            Remove-Item -Force $old.FullName
            Write-Log "removed $($old.Name)"
        }
    }
    Write-Log "done: $($missing.Count) copied, $([Math]::Min($local.Count, $Keep)) kept"
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    exit 1
}
