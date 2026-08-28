# restore.ps1 - restore a backup taken by scripts\backup.ps1.
# Run from the Windows host at the repo root, with the stack up.
#
#   .\scripts\restore.ps1 -Dir backups\20260828-035652
#
# OVERWRITES the current Postgres contents, the Qdrant collection, and
# artifacts\ with the backup's. Verified end-to-end 2026-08-28: flush ->
# restore reproduced point count, run ledger, artifact count, and identical
# search scores.
param(
    [Parameter(Mandatory = $true)][string]$Dir,
    [string]$Collection = "atlas_chunks",
    [string]$QdrantUrl = "http://localhost:17333",
    [string]$ApiUrl = "http://localhost:28080"
)
$ErrorActionPreference = 'Stop'

$Dir = (Resolve-Path $Dir).Path
$snapshot = Join-Path $Dir 'qdrant.snapshot'
$sql      = Join-Path $Dir 'atlas.sql'
$artSrc   = Join-Path $Dir 'artifacts'
foreach ($f in @($snapshot, $sql)) {
    if (-not (Test-Path $f)) { throw "missing from backup: $f" }
}
Write-Host "restore <- $Dir"

# --- Postgres (dump was taken with --clean, so this replaces cleanly) -------
Get-Content $sql | docker exec -i atlas-postgres psql -U atlas -d atlas -q -o /dev/null
if ($LASTEXITCODE -ne 0) { throw "psql restore failed (exit $LASTEXITCODE)" }
Write-Host "  postgres:  restored"

# --- Qdrant: upload snapshot. priority=snapshot makes the snapshot's data
#     authoritative over whatever is currently in the collection. ------------
$curl = (Get-Command curl.exe -ErrorAction SilentlyContinue)
if (-not $curl) { throw "curl.exe not found (needed for multipart snapshot upload)" }
& curl.exe -s -X POST "$QdrantUrl/collections/$Collection/snapshots/upload?priority=snapshot" `
    -F "snapshot=@$snapshot" --max-time 900 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "qdrant snapshot upload failed (exit $LASTEXITCODE)" }
$info = Invoke-RestMethod -Uri "$QdrantUrl/collections/$Collection"
Write-Host "  qdrant:    $($info.result.points_count) points, dim $($info.result.config.params.vectors.size)"

# --- Artifacts -------------------------------------------------------------
$artDst = Join-Path (Join-Path $PSScriptRoot '..') 'artifacts'
if (Test-Path $artSrc) {
    New-Item -ItemType Directory -Force -Path $artDst | Out-Null
    Get-ChildItem $artDst -Force | Remove-Item -Recurse -Force
    Copy-Item -Recurse -Force (Join-Path $artSrc '*') $artDst
    Write-Host "  artifacts: $((Get-ChildItem $artDst -Recurse -File).Count) files"
}

# --- Bounce the API so nothing in memory refers to pre-restore rows ---------
docker restart atlas-api | Out-Null
$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 3
    try { $ok = (Invoke-RestMethod -Uri "$ApiUrl/health").status -eq 'ok' } catch { $ok = $false }
} until ($ok -or (Get-Date) -gt $deadline)
if (-not $ok) { throw "API did not become healthy after restore" }
Write-Host "  atlas-api: restarted and healthy"

Write-Host ""
Write-Host "restore complete."
