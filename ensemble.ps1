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

.PORT
  -Port, else ENSEMBLE_PORT, else (all but start) the port the hub last
  started on, else 8765.

.STATE
  PID file: %USERPROFILE%\.ensemble\server.pid
  Port:     %USERPROFILE%\.ensemble\server.port (the port start last started the hub on)
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
$PortFile  = Join-Path $StateDir 'server.port'
$Log       = Join-Path $LogDir 'ensemble.log'

function Get-RecordedPort {
  # The port start last started the hub on ($null if none or not a port).
  try {
    $t = [string](Get-Content -LiteralPath $PortFile -ErrorAction Stop | Select-Object -First 1)
    $n = 0
    if ([int]::TryParse($t.Trim(), [ref]$n) -and $n -ge 1 -and $n -le 65535) { return $n }
  } catch {}
  return $null
}

# restart/stop/status find the hub where it last ran, unless a port was given
# (-Port, ENSEMBLE_PORT). start alone keeps the default.
$PortSource = '-Port'
if ($Port -eq 0) {
  $recorded = Get-RecordedPort
  if ($env:ENSEMBLE_PORT) { $Port = [int]$env:ENSEMBLE_PORT; $PortSource = 'ENSEMBLE_PORT' }
  elseif ($Action -ne 'start' -and $recorded) { $Port = $recorded; $PortSource = "recorded in $PortFile" }
  else { $Port = 8765; $PortSource = 'default' }
}
$Url = "http://127.0.0.1:$Port"

New-Item -ItemType Directory -Force -Path $StateDir, $LogDir | Out-Null

function Write-Note($Text) {
  # To stderr, and to the log with a time unless stderr already goes there
  # (the update the hub spawns sets ENSEMBLE_STDIO_IS_LOG). The hub holds the
  # log open, so share it rather than Add-Content (which would be refused).
  [Console]::Error.WriteLine($Text)
  if ($env:ENSEMBLE_STDIO_IS_LOG) { return }
  try {
    $fs = [IO.FileStream]::new($Log, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    try {
      $b = [Text.Encoding]::UTF8.GetBytes("[ensemble $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Text`r`n")
      $fs.Write($b, 0, $b.Length)
    } finally { $fs.Dispose() }
  } catch {}
}

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

function Get-PortPid {
  # Whoever listens on the port ($null if nobody).
  try {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
  } catch {}
  return $null
}

function Get-CommandLine($ProcId) {
  try {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcId" -ErrorAction Stop
    if ($p) { return [string]$p.CommandLine }
  } catch {}
  return ''
}

function Test-Ensemble($ProcId) {
  # True when the process is an Ensemble hub: this checkout's dashboard.py, or
  # a server on the port that answers with Ensemble's X-Ensemble-Stamp header.
  # The old claude-dashboard also listened on 8765 and must never be taken for
  # us (neither "Already running" nor stopped).
  $cmd = Get-CommandLine $ProcId
  if ($cmd -and $cmd.ToLower().Contains($Server.ToLower())) { return $true }
  # The probe says who holds the port, so it vouches only for the port's pid
  # (a stale pid file's number may now be some other program).
  if ($ProcId -ne (Get-PortPid)) { return $false }
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri "$Url/static/hl.js" -TimeoutSec 2 -ErrorAction Stop
    if ($r.Headers['X-Ensemble-Stamp']) { return $true }
  } catch {}
  return $false
}

function Get-RunningPid {
  # The live Ensemble PID ($null if none): the recorded PID, else an Ensemble
  # listening on the port. Something else on the port is not Ensemble.
  if (Test-Path $PidFile) {
    $p = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($p -and (Get-Process -Id $p -ErrorAction SilentlyContinue) -and (Test-Ensemble ([int]$p))) { return [int]$p }
  }
  $q = Get-PortPid
  if ($q -and (Test-Ensemble $q)) { return $q }
  return $null
}

function Get-ForeignPid {
  # PID of a process on the port that is NOT Ensemble ($null if none).
  $q = Get-PortPid
  if ($q -and -not (Test-Ensemble $q)) { return $q }
  return $null
}

function Get-PidDescription($ProcId) {
  $cmd = Get-CommandLine $ProcId
  if ($cmd) { return "pid ${ProcId}: $cmd" }
  return "pid $ProcId"
}

function Start-Dashboard {
  $existing = Get-RunningPid
  if ($existing) { Write-Host "Already running (pid $existing). $Url"; return }
  $other = Get-ForeignPid
  if ($other) {
    Write-Note "error: port $Port is held by another program ($(Get-PidDescription $other)), not Ensemble. Ensemble was not started."
    if ($PortSource -ne '-Port') { Write-Note "Port $Port came from: $PortSource." }
    if ((Get-CommandLine $other) -like '*\.claude\dashboard*') {
      Write-Note 'That is the old claude-dashboard; remove its task: schtasks /Delete /TN ClaudeDashboard /F'
    }
    Write-Note "Stop it, or start Ensemble on another port: .\ensemble.ps1 start -Port 8766"
    exit 1
  }
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
    [string]$Port | Out-File -Encoding ascii $PortFile
    Write-Host "Started (pid $($proc.Id)). $Url"
  } else {
    Write-Note "Failed to start on port $Port. Last log lines:"
    Get-Content $Log -Tail 20 -ErrorAction SilentlyContinue
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    exit 1
  }
}

function Stop-Dashboard {
  $p = Get-RunningPid
  if (-not $p) {
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    $other = Get-ForeignPid
    if ($other) { Write-Host "Not running. Port $Port is held by another program ($(Get-PidDescription $other)), not Ensemble: left alone." }
    else { Write-Host 'Not running.' }
    return
  }
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
  Write-Host ("  [info] autostart task (optional)  {0}" -f $(if ($task) { "Ensemble ($($task.State))" } else { "not installed - install-task.ps1 starts the hub at logon" }))

  Write-Host ""
  if ($script:fail -eq 0) { Write-Host "All checks passed." }
  else { Write-Host "$script:fail check(s) failed."; exit 1 }
}

# Dot-sourced (the tests do, to check the functions above): define, do not run.
if ($MyInvocation.InvocationName -eq '.') { return }

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
