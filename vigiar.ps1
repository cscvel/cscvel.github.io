# Roda junto com o servidor: a cada 60 s, se o banco do MatchZy mudou, publica as stats.
# Sai sozinho (com uma ultima publicacao) quando o servidor CS2 fecha.
$db = 'D:\cs2server\game\csgo\addons\counterstrikesharp\plugins\MatchZy\matchzy.db'
$pub = Join-Path $PSScriptRoot 'publicar.ps1'
function Get-DbStamp { (Get-ChildItem "$db*" -ErrorAction SilentlyContinue | Measure-Object LastWriteTimeUtc -Maximum).Maximum }
function Test-Server { [bool](Get-Process cs2 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like 'D:\cs2server\*' }) }

$last = Get-DbStamp
& $pub
while (Test-Server) {
    Start-Sleep 60
    $now = Get-DbStamp
    if ($now -ne $last) { $last = $now; & $pub }
}
& $pub
