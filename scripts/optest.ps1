param(
  [ValidateSet('deterministic','local_llm','lmstudio')]
  [string]$Mode = 'deterministic'
)

$ErrorActionPreference = 'Stop'

function Invoke-Compose($argsList) {
  Write-Host "[optest] docker compose $($argsList -join ' ')"
  docker compose @argsList
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

try {
  if ($Mode -eq 'lmstudio') {
    Invoke-Compose @('-f','docker-compose.optest.yml','--profile','lmstudio','up','--build','--abort-on-container-exit','--exit-code-from','e2e-lmstudio')
  } elseif ($Mode -eq 'local_llm') {
    Invoke-Compose @('-f','docker-compose.optest.yml','--profile','local_llm','up','--build','--abort-on-container-exit','--exit-code-from','e2e-local-llm')
  } else {
    Invoke-Compose @('-f','docker-compose.optest.yml','--profile','deterministic','up','--build','--abort-on-container-exit','--exit-code-from','e2e')
  }
}
finally {
  Invoke-Compose @('-f','docker-compose.optest.yml','down','-v')
}
