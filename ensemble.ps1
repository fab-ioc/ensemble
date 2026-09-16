<#
.SYNOPSIS
  ensemble: start/stop/restart/status/logs/open/doctor the Ensemble hub on
  Windows (PowerShell counterpart of the macOS/Linux `ensemble`
  bash script).

.USAGE
  .\ensemble.ps1 start   [-Port N]
  .\ensemble.ps1 stop
  .\ensemble.ps1 restart [-Port N]
  .\ensemble.ps1 status
  .\ensemble.ps1 logs        # tail -f the log file
  .\ensemble.ps1 open        # open the dashboard in the default browser

.STATE
  PID file: %USERPROFILE%\.ensemble\server.pid
  Logs:     %USERPROFILE%\.ensemble\logs\ensemble.log
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'open', 'doctor', 'help')]
  [string]$Action = 'status',
  [int]$Port = 0
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Server    = Join-Path $ScriptDir 'dashboard.py'
$StateDir  = Join-Path $env:USERPROFILE '.ensemble'
$LogDir    = Join-Path $StateDir 'logs'
$PidFile   = Join-Path $StateDir 'server.pid'
$Log       = Join-Path $LogDir 'ensemble.log'

if ($Port -eq 0) {
  $Port = if ($env:ENSEMBLE_PORT) { [int]$env:ENSEMBLE_PORT } else { 8765 }
}
$Url = "http://127.0.0.1:$Port"

New-Item -ItemType Directory -Force -Path $StateDir, $LogDir | Out-Null

function Resolve-Python {
  foreach ($c in 'py', 'python', 'python3') {
    $g = Get-Command $c -ErrorAction SilentlyContinue
    # Skip the Microsoft Store alias stub (it lives under WindowsApps and only
    # prompts to install).
    if ($g -and $g.Source -notlike '*\WindowsApps\*') { return $g.Source }
  }
  # Last resort: a real `py` even if the only thing on PATH is the launcher.
  $g = Get-Command 'py' -ErrorAction SilentlyContinue
  if ($g) { return $g.Source }
  return $null
}

function Resolve-PythonW {
  # Windowless interpreter (pythonw.exe) so no console window appears. Resolved
  # next to the real python.exe; falls back to pyw / console python.
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

function Get-RunningPid {
  # Prefer the recorded PID; fall back to whoever is listening on the port.
  if (Test-Path $PidFile) {
    $p = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue)) { return [int]$p }
  }
  try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
  } catch {}
  return $null
}

function Start-Dashboard {
  $existing = Get-RunningPid
  if ($existing) { Write-Host "Already running (pid $existing). $Url"; return }
  if (-not (Test-Path $Server)) { Write-Error "$Server not found"; exit 1 }
  $python = Resolve-PythonW
  if (-not $python) { Write-Error 'No real Python found (need python.org install / py launcher).'; exit 1 }
  # The headless agents need pywinpty (requirements.txt); install it for this
  # Python on a machine that does not have it yet, as the macOS script does.
  $py = Resolve-Python
  & $py -c 'import winpty' 2>$null
  if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing dependencies (pywinpty)...'
    & $py -m pip install --user -r (Join-Path $ScriptDir 'requirements.txt')
  }

  # pythonw.exe = no console window. The server writes its own log via --log
  # (pythonw has no stdio to redirect).
  $proc = Start-Process -FilePath $python `
    -ArgumentList @("`"$Server`"", '--port', $Port, '--log', "`"$Log`"") `
    -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru
  $proc.Id | Out-File -Encoding ascii $PidFile
  Start-Sleep -Milliseconds 500
  if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
    Write-Host "Started (pid $($proc.Id)). $Url"
  } else {
    Write-Host 'Failed to start. Last log lines:'
    Get-Content $Log -Tail 20 -ErrorAction SilentlyContinue
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 1
  }
}

function Stop-Dashboard {
  $p = Get-RunningPid
  if (-not $p) { Remove-Item $PidFile -ErrorAction SilentlyContinue; Write-Host 'Not running.'; return }
  try { Stop-Process -Id $p -Force -ErrorAction Stop } catch {}
  Remove-Item $PidFile -ErrorAction SilentlyContinue
  Write-Host "Stopped (was pid $p)."
}

function Invoke-Doctor {
  # Green/red health check of every prerequisite. Exits non-zero if any fail.
  $script:fail = 0
  function Check($label, $ok, $detail) {
    if ($ok) { Write-Host ("  [OK]   {0}  {1}" -f $label, $detail) }
    else     { Write-Host ("  [FAIL] {0}  {1}" -f $label, $detail); $script:fail++ }
  }
  Write-Host "ensemble doctor"
  Write-Host ""

  $py = Resolve-Python
  Check "python" ($null -ne $py) ($(if ($py) { $py } else { "not found (install from python.org)" }))

  $pyw = Resolve-PythonW
  $isWindowless = $pyw -and ($pyw -like '*pythonw.exe')
  Check "pythonw (windowless)" $isWindowless ($(if ($pyw) { $pyw } else { "not found" }))

  $hasPty = $false
  if ($py) { & $py -c 'import winpty' 2>$null; $hasPty = ($LASTEXITCODE -eq 0) }
  Check "pywinpty" $hasPty ($(if ($hasPty) { "installed" } else { "missing - py -m pip install -r requirements.txt" }))

  # Claude Code and Codex are each optional; the hub needs at least one.
  $claude = Get-Command claude -ErrorAction SilentlyContinue
  $codex = Get-Command codex -ErrorAction SilentlyContinue
  Check "claude or codex CLI" (($null -ne $claude) -or ($null -ne $codex)) ("claude: $(if ($claude) { $claude.Source } else { 'not on PATH' }); codex: $(if ($codex) { $codex.Source } else { 'not on PATH' })")

  $git = Get-Command git -ErrorAction SilentlyContinue
  Check "git" ($null -ne $git) ($(if ($git) { $git.Source } else { "not on PATH" }))

  $wt = Get-Command wt -ErrorAction SilentlyContinue
  Write-Host ("  [info] Windows Terminal (optional)  {0}" -f $(if ($wt) { $wt.Source } else { "not found - only needed to open a past session in a terminal window" }))

  Check "dashboard.py" (Test-Path $Server) $Server

  $running = Get-RunningPid
  Check "server running" ($null -ne $running) ($(if ($running) { "pid $running - $Url" } else { "not running (start it: ensemble.ps1 start)" }))

  if ($running) {
    $responds = $false
    try {
      $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/platform" -TimeoutSec 4
      $responds = ($r.StatusCode -eq 200)
    } catch {}
    Check "server responds" $responds "$Url/api/platform"
  }

  $task = Get-ScheduledTask -TaskName 'Ensemble' -ErrorAction SilentlyContinue
  Check "autostart task" ($null -ne $task) ($(if ($task) { "Ensemble ($($task.State))" } else { "not installed (optional: install-task.ps1)" }))

  Write-Host ""
  if ($script:fail -eq 0) { Write-Host "All checks passed." }
  else { Write-Host "$script:fail check(s) failed."; exit 1 }
}

switch ($Action) {
  'start'   { Start-Dashboard }
  'stop'    { Stop-Dashboard }
  'restart' { Stop-Dashboard; Start-Dashboard }
  'status'  {
    $p = Get-RunningPid
    if ($p) { Write-Host "Running (pid $p) - $Url"; Write-Host "Log: $Log" }
    else    { Write-Host 'Not running.' }
  }
  'logs'    {
    if (-not (Test-Path $Log)) { Write-Host "(no log yet at $Log)"; break }
    Get-Content $Log -Tail 50 -Wait
  }
  'open'    { Start-Process $Url }
  'doctor'  { Invoke-Doctor }
  'help'    { Get-Help $MyInvocation.MyCommand.Path -Detailed }
}
