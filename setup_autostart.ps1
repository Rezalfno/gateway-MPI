$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$taskName = 'SMM Industrial Gateway V7'
$python = & py -3.11-32 -c "import sys,struct; assert struct.calcsize('P')==4; print(sys.executable)"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) { throw 'Python 3.11 x86 not found' }
& $python -c "import paho.mqtt.client,pymodbus,serial"
if ($LASTEXITCODE -ne 0) { throw 'Run install.bat first under the gateway Windows account' }
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "Gateway Python: $python"
Write-Host "Use the SAME Windows account that has working STEP7/S7ONLINE: $account"
Write-Host 'Stop manual gateway first. Task runs at boot without login, using account password (not Windows Hello PIN).'
$credential = Get-Credential -UserName $account -Message 'Windows gateway account password (not PIN)'
if (-not $credential -or $credential.UserName -ne $account) { throw 'Use the same account as this setup session' }
$action = New-ScheduledTaskAction -Execute $python -Argument ('"' + (Join-Path $PSScriptRoot 'gateway.py') + '" --boot') -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$existing=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') { throw 'Stop existing scheduled task before replacing it' }
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password ($credential.GetNetworkCredential().Password) -RunLevel Limited -Force | Out-Null
} finally { $credential = $null }
Write-Host 'Task registered. Test now or restart Windows.'
if ((Read-Host 'Start task now? [y/N]') -eq 'y') { Start-ScheduledTask -TaskName $taskName }
Write-Host 'Log: gateway_boot.log (startup has a 20 second delay).'
