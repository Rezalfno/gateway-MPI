$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$c = Get-Content -Raw (Join-Path $PSScriptRoot 'remote_config.json') | ConvertFrom-Json
$python = & py -3.11-32 -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) { throw 'Python 3.11 x86 not found' }
# Validate configuration through same Python policy before opening port.
& $python -c "import json; from remote_access import RemotePolicy; RemotePolicy(json.load(open('remote_config.json')))"
if ($LASTEXITCODE -ne 0) { throw 'Invalid remote config' }
if (-not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $c.bind_ip -ErrorAction SilentlyContinue)) { throw 'Configured IP is not assigned to this Mini PC' }
Get-NetFirewallRule -Name 'SMM-IndustrialGateway-LAN' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'SMM-IndustrialGateway-LAN' -DisplayName 'SMM Industrial Gateway LAN only' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $c.port -LocalAddress $c.bind_ip -RemoteAddress $c.allowed_network -Profile Domain,Private -Program $python -EdgeTraversalPolicy Block | Out-Null
Write-Host 'Firewall rule created for approved office LAN. Public profile is not opened.'
