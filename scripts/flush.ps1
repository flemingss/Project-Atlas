# flush.ps1 - empty all Atlas data between testing rounds, WITHOUT touching
# infrastructure: containers stay up, schema stays in place, embeddings weight
# cache is preserved. Run from the Windows host at the repo root.
#
#   .\scripts\flush.ps1                 # wipe postgres tables + qdrant collections + artifacts/
#   .\scripts\flush.ps1 -SkipArtifacts  # keep artifacts/ on disk
#
# Scope: the `atlas` database only (dify/dify_plugin untouched), every Qdrant
# collection, and the contents of ./artifacts. This also clears DB-backed
# config overrides - after a flush you are back to config/*.yaml defaults.
param(
    [switch]$SkipArtifacts
)
$ErrorActionPreference = 'Stop'

# --- Postgres: truncate every table in the atlas DB's public schema ---------
# client_min_messages=warning: TRUNCATE ... CASCADE emits NOTICEs on stderr,
# which Windows PowerShell 5.1 wraps into a terminating NativeCommandError
# under $ErrorActionPreference = 'Stop'.
$sql = "SET client_min_messages = warning;`nSELECT format('TRUNCATE TABLE %I.%I RESTART IDENTITY CASCADE', schemaname, tablename) FROM pg_tables WHERE schemaname = 'public' \gexec"
$sql | docker exec -i atlas-postgres psql -U atlas -d atlas -v ON_ERROR_STOP=1 -q
if ($LASTEXITCODE -ne 0) { throw "postgres flush failed (exit $LASTEXITCODE)" }
Write-Host "postgres: all public tables truncated"

# --- Qdrant: delete every collection ----------------------------------------
$resp = Invoke-RestMethod -Uri 'http://localhost:17333/collections'
$collections = @($resp.result.collections)
if ($collections.Count -eq 0) {
    Write-Host "qdrant: no collections to delete"
} else {
    foreach ($c in $collections) {
        Invoke-RestMethod -Method Delete -Uri "http://localhost:17333/collections/$($c.name)" | Out-Null
        Write-Host "qdrant: deleted collection '$($c.name)'"
    }
}

# --- Artifacts ---------------------------------------------------------------
if (-not $SkipArtifacts) {
    if (Test-Path artifacts) {
        Get-ChildItem artifacts -Force | Remove-Item -Recurse -Force
        Write-Host "artifacts/: emptied"
    }
} else {
    Write-Host "artifacts/: kept (-SkipArtifacts)"
}

# --- Restart the API so no in-memory state outlives the wiped rows ----------
docker restart atlas-api | Out-Null
Write-Host "atlas-api: restarted (drops cached/in-memory references to wiped rows)"

Write-Host "flush complete - stack still running; schema intact."

