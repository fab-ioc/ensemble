<#
.SYNOPSIS
  Register a Scheduled Task so ensemble starts at logon and keeps running
  (the Windows counterpart of install-launchd.sh). Runs in your interactive user
  session so it can drive Windows Terminal.

.USAGE
  .\install-task.ps1            # install + start
  .\install-task.ps1 uninstall # remove
  .\install-task.ps1 status    # show task + recent log

.NOTES
  Task name: Ensemble
  Logs:      %USERPROFILE%\.ensemble\logs\ensemble.log
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('install', 'uninstall', 'status')]
  [string]$Action = 'install',
  [int]$Port = 0,
  # Optional Jira integration (opt-in). Provide -JiraBase to enable it; the
  # browse URL, e.g. "https://your-org.atlassian.net/browse/". -JiraPrefixes is
  # a comma-separated project-key list, e.g. "PTECH,PLAT".
  [string]$JiraBase = '',
  [string]$JiraPrefixes = ''
)

$ErrorActionPreference = 'Stop'

$TaskName  = 'Ensemble'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Server    = Join-Path $ScriptDir 'dashboard.py'
$StateDir  = Join-Path $env:USERPROFILE '.ensemble'
$Log       = Join-Path $StateDir 'logs\ensemble.log'

if ($Port -eq 0) {
  $Port = if ($env:ENSEMBLE_PORT) { [int]$env:ENSEMBLE_PORT } else { 8765 }
}

function Resolve-Python {
  foreach ($c in 'py', 'python', 'python3') {
    $g = Get-Command $c -ErrorAction SilentlyContinue
    if ($g -and $g.Source -notlike '*\WindowsApps\*') { return $g.Source }
  }
  $g = Get-Command 'py' -ErrorAction SilentlyContinue
  if ($g) { return $g.Source }
  return $null
}

function Resolve-PythonW {
  # Windowless interpreter (pythonw.exe) so the task runs with no console window.
  $py = Resolve-Python
  if ($py) {
    try {
      $exe = (& $py -c 'import sys; print(sys.executable)').Trim()
      $pyw = Join-Path (Split-Path $exe) 'pythonw.exe'
      if (Test-Path $pyw) { return $pyw }
    } catch {}
  }
  $g = Get-Command pythonw, pyw -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($g) { return $g.Source }
  return $py
}

switch ($Action) {
  'install' {
    $python = Resolve-PythonW
    if (-not $python)            { Write-Error 'No real Python found (need python.org install / py launcher).'; exit 1 }
    if (-not (Test-Path $Server)) { Write-Error "$Server not found"; exit 1 }
    New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null

    # Optional Jira config → dashboard settings.json (opt-in; merges, doesn't clobber).
    if ($JiraBase) {
      $settingsFile = Join-Path $StateDir 'settings.json'
      $ht = @{}
      if (Test-Path $settingsFile) {
        try {
          $existing = Get-Content $settingsFile -Raw -Encoding utf8 | ConvertFrom-Json -ErrorAction Stop
          $existing.PSObject.Properties | ForEach-Object { $ht[$_.Name] = $_.Value }
        } catch {}
      }
      $ht['jiraEnabled'] = $true
      $ht['jiraBase'] = $JiraBase
      if ($JiraPrefixes) {
        $ht['jiraPrefixes'] = @($JiraPrefixes -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
      }
      ($ht | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 $settingsFile
      Write-Host "Jira enabled -> $JiraBase"
    }

    # NB: avoid a local named $action — PowerShell vars are case-insensitive, so
    # it would clobber the $Action parameter. pythonw.exe + --log = no console
    # window, logs still captured to file.
    $taskAction = New-ScheduledTaskAction -Execute $python `
                    -Argument "`"$Server`" --port $Port --log `"$Log`"" -WorkingDirectory $ScriptDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # Interactive so the server can launch/control Windows Terminal windows.
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                   -DontStopIfGoingOnBatteries -StartWhenAvailable `
                   -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
      -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName

    Write-Host "Installed scheduled task: $TaskName"
    Write-Host "Logs: $Log"
    Write-Host "URL:  http://127.0.0.1:$Port"
  }
  'uninstall' {
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch {}
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Uninstalled scheduled task: $TaskName"
  }
  'status' {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Write-Host "Not installed (no task '$TaskName')"; break }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host "Task:        $TaskName"
    Write-Host "State:       $($task.State)"
    Write-Host "Last run:    $($info.LastRunTime)  (result 0x$('{0:X}' -f $info.LastTaskResult))"
    Write-Host ''
    Write-Host 'Recent log lines:'
    if (Test-Path $Log) { Get-Content $Log -Tail 10 } else { Write-Host '  (no log yet)' }
  }
}
