# backup.ps1 - point-in-time backup of everything Atlas cannot regenerate.
# Run from the Windows host at the repo root, with the stack up.
#
#   .\scripts\backup.ps1                      # -> backups\<timestamp>\
#   .\scripts\backup.ps1 -OutDir D:\atlas-bk  # custom location
#
# Captures the three stores that hold irreplaceable state:
#   qdrant.snapshot  - vector collection (Qdrant snapshot API)
#   atlas.sql        - Postgres dump (--clean, so restore is idempotent)
#   artifacts\       - source PDFs, VLM page output, markdown projections
#
# NOT captured (regenerable or operator-local): the embeddings weight cache,
# container images, config\*.yaml (seed from *.example).
#
# Restore with scripts\restore.ps1 -Dir <the directory this printed>.
param(
    [string]$OutDir = "",
    [string]$Collection = "atlas_chunks",
    [string]$QdrantUrl = "http://localhost:17333"
)
$ErrorActionPreference = 'Stop'

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $OutDir) { $OutDir = Join-Path (Join-Path $PSScriptRoot '..') "backups\$stamp" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
Write-Host "backup -> $OutDir"

# --- Qdrant: snapshot, then download it to the host ------------------------
$snap = Invoke-RestMethod -Method Post -Uri "$QdrantUrl/collections/$Collection/snapshots?wait=true"
$snapName = $snap.result.name
Invoke-WebRequest -Uri "$QdrantUrl/collections/$Collection/snapshots/$snapName" `
    -OutFile (Join-Path $OutDir 'qdrant.snapshot') | Out-Null
Write-Host "  qdrant:    $snapName ($([math]::Round((Get-Item (Join-Path $OutDir 'qdrant.snapshot')).Length/1MB,1)) MB)"
# The in-container copy is redundant once downloaded, and they accumulate.
try {
    Invoke-RestMethod -Method Delete -Uri "$QdrantUrl/collections/$Collection/snapshots/$snapName" | Out-Null
} catch { Write-Host "  (could not remove in-container snapshot; harmless)" }

# --- Postgres --------------------------------------------------------------
# 2>$null on the native call: pg_dump writes progress to stderr, which
# PowerShell would otherwise promote to a terminating error.
docker exec atlas-postgres pg_dump -U atlas -d atlas --clean --if-exists `
    | Out-File -FilePath (Join-Path $OutDir 'atlas.sql') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed (exit $LASTEXITCODE)" }
Write-Host "  postgres:  atlas.sql ($([math]::Round((Get-Item (Join-Path $OutDir 'atlas.sql')).Length/1KB,1)) KB)"

# --- Artifacts -------------------------------------------------------------
$artifacts = Join-Path (Join-Path $PSScriptRoot '..') 'artifacts'
if (Test-Path $artifacts) {
    Copy-Item -Recurse -Force $artifacts (Join-Path $OutDir 'artifacts')
    $n = (Get-ChildItem (Join-Path $OutDir 'artifacts') -Recurse -File).Count
    Write-Host "  artifacts: $n files"
} else {
    Write-Host "  artifacts: (none)"
}

Write-Host ""
Write-Host "backup complete: $OutDir"
Write-Host "restore with: .\scripts\restore.ps1 -Dir `"$OutDir`""
