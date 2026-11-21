# Exporta as stats do MatchZy e publica no GitHub Pages se algo mudou.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot 'publicar.log'
function Write-Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Add-Content $log -Encoding UTF8 }

$out = & python -I (Join-Path $PSScriptRoot 'export.py') 2>&1
Write-Log "export: $out"
git add docs/data.json docs/index.html 2>$null
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { return }   # nada novo
git commit -q -m "stats: atualiza $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
git push -q origin main 2>&1 | ForEach-Object { Write-Log "push: $_" }
if ($LASTEXITCODE -eq 0) { Write-Log 'publicado' } else { Write-Log "push falhou ($LASTEXITCODE)" }
