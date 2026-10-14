# Plain restart of the Ensemble hub: stop it and start it again on the code
# already on disk. Nothing is fetched, reset or pulled, so a merge that is not
# pushed yet survives (the Update button resets main to its upstream).
#
# Started by the hub (dashboard.trigger_restart, POST /api/restart or the PO's
# ensemble_restart_hub tool) through WMI, so it is outside the hub's process
# tree and outlives it. The hub runs a copy from ~/.ensemble/_launch; the plan
# (a JSON file next to it) says how the hub was started. Order:
#   1. Preflight: the code on disk must start and serve on a spare port, or
#      the running hub is not touched.
#   2. Wait out the grace period, so the caller's reply reaches the user.
#   3. Have the hub write down who is running and who is in the middle of a
#      turn (POST /api/restart/snapshot), stop it (through its scheduled task
#      when that is how it runs) and start it again the same way; wait until
#      it answers.
#   4. Have the new hub bring back every room that was running (POST
#      /api/restart/restore: an idle agent is typed nothing, one that was in
#      the middle of a turn one line) and type the PO what to do next.
# Keep this file ASCII: Windows PowerShell 5.1 reads a script without a BOM in
# the ANSI code page.
param([Parameter(Mandatory = $true)][string]$Plan)

$cfg = Get-Content -LiteralPath $Plan -Raw -Encoding UTF8 | ConvertFrom-Json
Remove-Item -LiteralPath $Plan -Force -ErrorAction SilentlyContinue
# WMI started this with the user's default environment; take the hub's (a test
# hub's USERPROFILE, ENSEMBLE_* and PATH), which everything started here inherits.
foreach ($p in $cfg.env.PSObject.Properties) {
  [Environment]::SetEnvironmentVariable($p.Name, [string]$p.Value, 'Process')
}
$log  = [string]$cfg.log
$port = [int]$cfg.port
$base = "http://127.0.0.1:$port"
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $log) | Out-Null
function L($m) { "$((Get-Date).ToString('s')) [restart] $m" | Out-File -FilePath $log -Append -Encoding utf8 }
function Ok($u, [int]$timeoutSec = 30) { try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec $timeoutSec; return ($r.StatusCode -eq 200) } catch { return $false } }
# Up and past its start-up steps (ED-227): a hub listens before them, so
# /api/platform alone no longer says the code starts. A hub from before
# /api/health is up once /api/platform answers.
function Started($b) {
  try {
    $r = Invoke-WebRequest -Uri "$b/api/health" -UseBasicParsing -TimeoutSec 5
    return ((($r.Content | ConvertFrom-Json).phase) -ne 'starting')
  } catch {
    $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -eq 404) { return (Ok "$b/api/platform") }
    return $false
  }
}
function Q($s) { '"' + ([string]$s -replace '"', '\"') + '"' }
function Post($path, $obj) {
  $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress))
  Invoke-WebRequest -Uri "$base$path" -Method POST -ContentType 'application/json; charset=utf-8' -Body $bytes -UseBasicParsing -TimeoutSec 170
}
# The processes serving the hub on a port: its listener, and any whose command
# line names that port. Never anything else. A checkout's hub is python running
# dashboard.py; the built app's is Ensemble.exe itself ($cfg.script empty).
$hubMatch = if ($cfg.script) { 'dashboard.py' } else { [IO.Path]::GetFileName([string]$cfg.python) }
function IsHub($p) {
  if ($cfg.script) { return ($p.Name -like 'python*') -and ($p.CommandLine -like '*dashboard.py*') }
  return ($p.Name -eq $hubMatch) -and ($p.CommandLine -notlike '*--run *')
}
function HubProcs([int]$onPort) {
  $ids = @()
  try { $ids += @(Get-NetTCPConnection -LocalPort $onPort -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique) } catch {}
  $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { IsHub $_ })
  $ids += @($procs | Where-Object { $_.CommandLine -match "--port\s+$onPort(\s|$)" } | Select-Object -ExpandProperty ProcessId)
  $procs | Where-Object { $ids -contains $_.ProcessId }
}
# Who listens on a port, for the log: "pid 9120 python.exe: python -m unittest ...".
function Listeners([int]$onPort) {
  $ids = @()
  try { $ids = @(Get-NetTCPConnection -LocalPort $onPort -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique) } catch {}
  @($ids | ForEach-Object {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$_)" -ErrorAction SilentlyContinue
    if ($p) { "pid $($p.ProcessId) $($p.Name): $($p.CommandLine)" } else { "pid $_" }
  })
}
function Head { try { return (& git -C $cfg.repo rev-parse HEAD 2>$null) } catch { return '?' } }
# The hub's restart lease refuses a second restart while this one runs. When the
# hub is left alone (a failed preflight) it is dropped, so trying again is fine;
# only this restart's own lease, never a newer one.
function DropLease {
  if (-not $cfg.leasePath) { return }
  try {
    $l = Get-Content -LiteralPath $cfg.leasePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($l.id -eq $cfg.leaseId) { Remove-Item -LiteralPath $cfg.leasePath -Force -ErrorAction Stop; L "restart lease dropped" }
  } catch {}
}
# The lease expires RESTART_BUSY_S after its "at"; renewed at every wait, it
# cannot expire while this restart runs, and the last renewal (hub up) refuses
# another restart for that long after it.
function RenewLease {
  if (-not $cfg.leasePath) { return }
  try {
    $l = Get-Content -LiteralPath $cfg.leasePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($l.id -ne $cfg.leaseId) { return }
    $at = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0).ToString([Globalization.CultureInfo]::InvariantCulture)
    $json = '{"id": "' + $cfg.leaseId + '", "at": ' + $at + ', "pid": ' + $PID + '}'
    [IO.File]::WriteAllText([string]$cfg.leasePath, $json, (New-Object Text.UTF8Encoding $false))
  } catch {}
}
function Nap([int]$s) { Start-Sleep -Seconds $s; RenewLease }
# An error nothing catches ends this script: say so in the log first, and let
# go of the lease so the hub (which watches this process) and a retry are free.
trap {
  try { L "HELPER ERROR - $($_.Exception.Message) (line $($_.InvocationInfo.ScriptLineNumber)); stopped here" } catch {}
  try { DropLease } catch {}
  # Past the stop the hub is down: start it the plain way rather than leave it
  # so. Never a second hub (ED-230): not when this script already started one
  # (the task or directly), nor when something listens on the port.
  if ($hubStopped) {
    try {
      $held = @(Listeners $port)
      if ($hubStarted) { L "a hub was already started; not starting another" }
      elseif ($held.Count) { L "port $port is held ($($held -join '; ')); not starting a hub" }
      else { L "HUB DOWN after the error; starting it directly"; StartDirect }
    } catch {}
  }
  exit 1
}
$hubStopped = $false
$hubStarted = $false

$t0 = Get-Date
$head0 = Head
L "=== plain restart of the hub on port $port (pid $($cfg.hubPid)); mode $(if ($cfg.keepAgents) { 'hub only' } else { 'classic' }); code at $($cfg.repo) $head0 ==="

# --- 1. Preflight: the code on disk must start and serve on a spare port. ---
$pf = [int]$cfg.preflightPort
$pfArgs = @()
if ($cfg.script) { $pfArgs += (Q $cfg.script) }
$pfArgs += @('--port', "$pf", '--log', (Q $cfg.preflightLog))
try {
  $env:ENSEMBLE_PTY_PREFLIGHT = '1'
  $pfp = Start-Process -FilePath $cfg.python -ArgumentList $pfArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -PassThru -ErrorAction Stop
  Remove-Item Env:ENSEMBLE_PTY_PREFLIGHT -ErrorAction SilentlyContinue
} catch {
  Remove-Item Env:ENSEMBLE_PTY_PREFLIGHT -ErrorAction SilentlyContinue
  L "PREFLIGHT FAILED - could not start $($cfg.python): $_; the hub was NOT touched"
  DropLease
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}
$pfUp = $false
for ($i = 0; $i -lt 60 -and -not $pfUp -and -not $pfp.HasExited; $i++) { Nap 2; $pfUp = Started "http://127.0.0.1:$pf" }
# /api/sessions walks every held transcript on a cold process (its cost/turn
# caches are empty); measured live at 30-32s against this hub's real history,
# right on top of the old 30s cap - the exact cause of "PREFLIGHT FAILED" on an
# otherwise healthy hub. Give it real headroom; a genuinely broken /api/sessions
# still fails preflight, just not until $sessTimeoutSec runs out.
# 10-09: 88-94s measured cold against the real history (90s cap failed a
# healthy restart); 170s stays under the 180s restart lease, which is only
# renewed between waits.
# ED-200: what a hub read of each transcript is kept on disk (filefacts);
# with it a cold call takes 9 s, without it (the first start after that
# change, or a cache version bump) 52 s. The cap stays at 170 s: 60 s would
# not leave 3x headroom over the start without the cache.
$sessTimeoutSec = 170
if ($env:ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S) {
  try { $sessTimeoutSec = [int]$env:ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S } catch {}
}
$pfOk = $pfUp -and (Ok "http://127.0.0.1:$pf/api/sessions?n=5" $sessTimeoutSec) -and (Ok "http://127.0.0.1:$pf/?startup=skip") -and (Ok "http://127.0.0.1:$pf/api/projects")
# A hub line must reach an agent: the preflight hub types one into a throwaway
# agent on its own PTY host (P190: from 10-13 18:11 to 10-14 07:31 every line
# the hub typed was refused, and nothing said so).
if ($pfOk) {
  $typed = $false
  RenewLease   # /api/sessions may have taken most of the lease
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes((@{ lease = [string]$cfg.leaseId } | ConvertTo-Json -Compress))
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$pf/api/restart/typing-check" -Method POST -ContentType 'application/json; charset=utf-8' -Body $bytes -UseBasicParsing -TimeoutSec 60
    $tc = $r.Content | ConvertFrom-Json
    $typed = [bool]$tc.ok
    L "preflight typing check: $($tc.detail)"
  } catch { L "preflight typing check could not run: $_" }
  $pfOk = $typed
}
try {
  $bytes = [Text.Encoding]::UTF8.GetBytes((@{ lease = [string]$cfg.leaseId } | ConvertTo-Json -Compress))
  Invoke-WebRequest -Uri "http://127.0.0.1:$pf/api/restart/host-stop" -Method POST -ContentType 'application/json; charset=utf-8' -Body $bytes -UseBasicParsing -TimeoutSec 10 | Out-Null
} catch {}
try { Stop-Process -Id $pfp.Id -Force -ErrorAction Stop } catch {}
HubProcs $pf | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }
if (-not $pfOk) {
  L "PREFLIGHT FAILED - the code on disk does not start or serve on port $pf; the hub was NOT touched. See $($cfg.preflightLog)"
  DropLease
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}
L "preflight passed on port $pf"

# --- 2. Give the caller's reply time to reach the user before the hub goes down. ---
$left = [double]$cfg.graceSeconds - ((Get-Date) - $t0).TotalSeconds
RenewLease
if ($left -gt 0) { L "waiting $([int][math]::Ceiling($left))s so the reply reaches the user" }
while ($left -gt 0) { $s = [int][math]::Min(10, [math]::Ceiling($left)); Nap $s; $left -= $s }

# --- 3. Stop the hub and start it again the way it was started. ---
# First the hub's last word on who is running and who is mid-turn. A hub from
# before this existed answers 404: the rooms of the plan are then resumed the
# old way in step 4.
if ($cfg.keepAgents) {
  L "asking the hub to save its warm caches before the stop"
  try {
    $r = Post '/api/restart/prepare' @{ lease = [string]$cfg.leaseId }
    L "warm caches prepared before the stop: HTTP $($r.StatusCode)"
  } catch { L "warm caches could not be prepared: $_" }
}
if (-not $cfg.keepAgents) {
  $snapshotOk = $false
  for ($attempt = 0; $attempt -lt 3 -and -not $snapshotOk; $attempt++) {
    try {
      $r = Post '/api/restart/snapshot' @{ lease = [string]$cfg.leaseId }
      $snapshotOk = ($r.StatusCode -eq 200)
      if ($snapshotOk) { L "snapshot before the stop: HTTP $($r.StatusCode) $($r.Content)" }
    } catch { L "snapshot attempt $($attempt + 1) failed: $_"; Nap 1 }
  }
  if (-not $snapshotOk) {
    L "classic restart cancelled: no confirmed fresh snapshot; hub and PTY host remain running"
    DropLease
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
  }
}
$hostPin = ''
if (-not $cfg.keepAgents) {
  $stateDir = Split-Path -Parent ([string]$cfg.leasePath)
  $descriptor = Join-Path $stateDir 'pty-host.json'
  $hostPin = Join-Path $stateDir "_launch\host-pin-$($cfg.leaseId).json"
  try {
    if ($cfg.ptyHostVersion -and -not (Test-Path -LiteralPath $descriptor)) {
      throw 'old PTY host identity is missing'
    }
    if (Test-Path -LiteralPath $descriptor) { Copy-Item -LiteralPath $descriptor -Destination $hostPin -Force -ErrorAction Stop }
    else { [IO.File]::WriteAllText($hostPin, '{"absent":true}', (New-Object Text.UTF8Encoding $false)) }
  } catch {
    L "classic restart cancelled: could not pin PTY host identity: $_"
    DropLease
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    exit 1
  }
}
# Defined before the stop: the trap may need it the moment the hub is down.
$hubArgs = @()
if ($cfg.script) { $hubArgs += (Q $cfg.script) }
$hubArgs += @($cfg.args | ForEach-Object { Q $_ })
function StartDirect {
  try {
    Start-Process -FilePath $cfg.python -ArgumentList $hubArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -ErrorAction Stop | Out-Null
    $script:hubStarted = $true
    L "started $($cfg.python) $($hubArgs -join ' ')"
  } catch { L "start err: $_" }
}
$useTask = $false
if (-not $cfg.noTask) {
  $task = Get-ScheduledTask -TaskName $cfg.taskName -ErrorAction SilentlyContinue
  if ($task) {
    $taskArgs = [string](@($task.Actions)[0].Arguments)
    $taskRuns = $taskArgs + ' ' + [string](@($task.Actions)[0].Execute)
    $useTask = ($taskRuns -like "*$hubMatch*") -and ($taskArgs -match "--port\s+$port(\s|$)")
  }
}
if ($useTask) { L "through the scheduled task '$($cfg.taskName)'" } else { L "the hub process directly (not the scheduled task)" }
if ($useTask) {
  try { Stop-ScheduledTask -TaskName $cfg.taskName -ErrorAction Stop; L "stopped task" } catch { L "stop-task err: $_" }
  Start-Sleep -Seconds 3
}
$victims = @(HubProcs $port) + @(Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$cfg.hubPid)" -ErrorAction SilentlyContinue |
                                 Where-Object { IsHub $_ })
foreach ($v in ($victims | Where-Object { $_ } | Sort-Object ProcessId -Unique)) {
  try { Stop-Process -Id $v.ProcessId -Force -ErrorAction Stop; L "stopped pid $($v.ProcessId)" } catch { L "stop pid $($v.ProcessId): $_" }
}
$hubStopped = $true
$free = $false
$oldGone = $false
for ($i = 0; $i -lt 15 -and -not ($free -and $oldGone); $i++) {
  Nap 1
  $free = -not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  $old = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$cfg.hubPid)" -ErrorAction SilentlyContinue | Where-Object { IsHub $_ }
  $oldGone = -not $old
}
if (-not ($free -and $oldGone)) {
  L "old hub PID or port $port is still active; PTY host left running"
  foreach ($h in @(Listeners $port)) { L "port $port is held by $h" }
  # The task's hub exits at once while the port is taken (it binds it
  # exclusively), and the task tries again later.
  if ($useTask) { try { Start-ScheduledTask -TaskName $cfg.taskName -ErrorAction Stop; $hubStarted = $true; L "started task" } catch { L "start-task err: $_" } }
  DropLease
  if ($hostPin) { Remove-Item -LiteralPath $hostPin -Force -ErrorAction SilentlyContinue }
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}
if (-not $cfg.keepAgents) {
  $stateDir = Split-Path -Parent ([string]$cfg.leasePath)
  $hostArgs = @()
  if ($cfg.script) { $hostArgs += (Q (Join-Path $cfg.repo 'backends\ptyhost.py')) }
  else { $hostArgs += @('--run', 'pty_host') }
  $hostArgs += @('--stop', (Q $stateDir))
  if ($hostPin) { $hostArgs += (Q $hostPin) }
  try {
    $stopper = Start-Process -FilePath $cfg.python -ArgumentList $hostArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
    $hostGone = ($stopper.ExitCode -eq 0)
  } catch { $hostGone = $false; L "PTY host stop failed: $_" }
  if (-not $hostGone) {
    L "PTY host did not stop cleanly; force-killing pinned process tree"
    $forceArgs = @()
    if ($cfg.script) { $forceArgs += (Q (Join-Path $cfg.repo 'backends\ptyhost.py')) }
    else { $forceArgs += @('--run', 'pty_host') }
    $forceArgs += @('--force-stop', (Q $hostPin))
    try {
      $forced = Start-Process -FilePath $cfg.python -ArgumentList $forceArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -Wait -PassThru -ErrorAction Stop
      L "pinned PTY host force-stop exit code: $($forced.ExitCode); starting hub anyway"
    } catch { L "pinned PTY host force-stop failed: $_; starting hub anyway" }
  } else {
    L "classic PTY host process confirmed gone"
  }
  if ($hostPin) { Remove-Item -LiteralPath $hostPin -Force -ErrorAction SilentlyContinue }
}
$up = $false
if ($useTask) {
  try { Start-ScheduledTask -TaskName $cfg.taskName -ErrorAction Stop; $hubStarted = $true; L "started task" } catch { L "start-task err: $_" }
  for ($i = 0; $i -lt 20 -and -not $up; $i++) { Nap 2; $up = Started $base }
  if (-not $up) { L "not up after the task; starting it directly the way it ran"; StartDirect }
} else {
  StartDirect
}
for ($i = 0; $i -lt 30 -and -not $up; $i++) { Nap 2; $up = Started $base }
# Up again: from here an error must not make the trap start a second hub.
if ($up) { $hubStopped = $false; RenewLease }
L "hub up: $up; code at $(Head) (was $head0)"
if (-not $up) {
  L "HUB STILL DOWN - start the '$($cfg.taskName)' scheduled task by hand"
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}

# --- 4. Bring the rooms back and tell the PO what to do next. ---
if ($cfg.keepAgents) {
  L "hub-only restart complete; PTY host and agents stayed running"
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 0
}
Start-Sleep -Seconds 4
# The hub does it from the snapshot and logs each room here itself. What it did
# not bring back of the plan's own rooms (no snapshot: the hub that stopped was
# older than this) is resumed the old way.
$back = @()
try {
  $r = Post '/api/restart/restore' @{ lease = [string]$cfg.leaseId }
  $res = $r.Content | ConvertFrom-Json
  $back = @($res.rooms | Where-Object { $_.outcome -like 'brought back*' -or $_.outcome -like 'already running*' } | ForEach-Object { $_.roomId })
  L "restore: HTTP $($r.StatusCode), $(@($res.rooms).Count) room(s) in the snapshot, $($back.Count) running again. $($res.note)"
} catch { L "restore failed: $_" }
foreach ($room in @($cfg.resumeRooms)) {
  if (-not $room -or ($back -contains $room)) { continue }
  try { $r = Post '/api/room/resume' @{ roomId = $room; quiet = $true }; L "resume ${room}: HTTP $($r.StatusCode)" } catch { L "resume $room failed: $_" }
}
if ($cfg.wakeRoom) {
  $pty = $null
  for ($i = 0; $i -lt 30 -and -not $pty; $i++) {
    Start-Sleep -Seconds 2
    try {
      $list = Invoke-RestMethod -Uri "$base/api/ptys" -TimeoutSec 10
      $pty = ($list | Where-Object { $_.meta.room -eq $cfg.wakeRoom -and $_.alive } | Select-Object -First 1).id
    } catch {}
  }
  L "PO pty: $pty"
  if ($pty) {
    Start-Sleep -Seconds 15
    try {
      # hub: this line and its Enter are the restart's, not a person's answer
      # to anything the PO asked.
      Post '/api/pty/input' @{ id = $pty; data = [string]$cfg.wakeText; hub = $true } | Out-Null
      Start-Sleep -Milliseconds 500
      Post '/api/pty/input' @{ id = $pty; data = "`r"; hub = $true } | Out-Null
      L "woke the PO"
    } catch { L "wake failed: $_" }
  }
}
L "=== done ==="
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
