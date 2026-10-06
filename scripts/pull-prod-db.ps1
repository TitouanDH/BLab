<#
.SYNOPSIS
    Copy the production database into the local development Postgres.

.DESCRIPTION
    Dumps the prod Postgres container over SSH, downloads the dump to .local/
    (gitignored; it contains real users and tokens) and restores it into the
    db service of docker-compose.dev.yml, replacing whatever was there.
    Production is only read.

.EXAMPLE
    .\scripts\pull-prod-db.ps1 -SshTarget admin@10.69.144.180
    .\scripts\pull-prod-db.ps1 -SshTarget admin@10.69.144.180 -SkipDownload   # re-restore the last dump
#>
param(
    [Parameter(Mandatory = $true)][string]$SshTarget,
    [string]$DbContainer = "",          # prod Postgres container; found automatically if empty
    [string]$RemoteDocker = "docker",   # use "sudo docker" if the SSH user is not in the docker group
    [switch]$SkipDownload
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$localDir = Join-Path $repo ".local"
$dumpFile = Join-Path $localDir "prod-snapshot.dump"
$devCompose = Join-Path $repo "docker-compose.dev.yml"
$remoteTmp = "/tmp/blab_prod_snapshot.dump"

function Invoke-Checked([string]$what, [scriptblock]$block) {
    & $block
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

if (-not $SkipDownload) {
    if (-not $DbContainer) {
        $DbContainer = (ssh $SshTarget "$RemoteDocker ps --format '{{.Names}}' --filter ancestor=postgres:16" | Select-Object -First 1)
        if (-not $DbContainer) { throw "No postgres:16 container found on $SshTarget; pass -DbContainer." }
    }
    Write-Host "Dumping blab_db from $DbContainer on $SshTarget..."
    Invoke-Checked "Remote pg_dump" {
        ssh $SshTarget "$RemoteDocker exec $DbContainer pg_dump -U admin -d blab_db -Fc -f $remoteTmp && $RemoteDocker cp ${DbContainer}:$remoteTmp $remoteTmp && $RemoteDocker exec $DbContainer rm -f $remoteTmp"
    }
    New-Item -ItemType Directory -Force $localDir | Out-Null
    Invoke-Checked "Download" { scp "${SshTarget}:$remoteTmp" $dumpFile }
    ssh $SshTarget "rm -f $remoteTmp" | Out-Null
}

if (-not (Test-Path $dumpFile)) { throw "No dump at $dumpFile" }

Write-Host "Restoring into the local dev database..."
Invoke-Checked "Starting dev db" { docker compose -f $devCompose up -d --wait db }
Invoke-Checked "Copying dump" { docker compose -f $devCompose cp $dumpFile db:/tmp/snapshot.dump }
# pg_restore reports harmless warnings (e.g. missing objects on --clean) with a non-zero exit code.
docker compose -f $devCompose exec -T db pg_restore --clean --if-exists --no-owner --no-privileges -U blab_dev -d blab_dev /tmp/snapshot.dump
if ($LASTEXITCODE -ne 0) { Write-Warning "pg_restore exited with $LASTEXITCODE; check the messages above." }
docker compose -f $devCompose exec -T db rm -f /tmp/snapshot.dump | Out-Null

Write-Host "Done. Local dev database now mirrors production as of $(Get-Date -Format 'yyyy-MM-dd HH:mm')."
