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
function Q($s) { '"' + ([string]$s -replace '"', '\"') + '"' }
function Post($path, $obj) {
  $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress))
  Invoke-WebRequest -Uri "$base$path" -Method POST -ContentType 'application/json; charset=utf-8' -Body $bytes -UseBasicParsing -TimeoutSec 60
}
# The python processes serving dashboard.py on a port: its listener, and any
# whose command line names that port. Never anything else.
function HubProcs([int]$onPort) {
  $ids = @()
  try { $ids += @(Get-NetTCPConnection -LocalPort $onPort -State Listen -ErrorAction Stop | Select-Object -ExpandProperty OwningProcess -Unique) } catch {}
  $procs = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue)
  $ids += @($procs | Where-Object { $_.CommandLine -match "dashboard\.py.*--port\s+$onPort(\s|$)" } | Select-Object -ExpandProperty ProcessId)
  $procs | Where-Object { ($ids -contains $_.ProcessId) -and ($_.CommandLine -like '*dashboard.py*') }
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

$t0 = Get-Date
$head0 = Head
L "=== plain restart of the hub on port $port (pid $($cfg.hubPid)); code at $($cfg.repo) $head0; no fetch, reset or pull ==="

# --- 1. Preflight: the code on disk must start and serve on a spare port. ---
$pf = [int]$cfg.preflightPort
$pfArgs = @((Q $cfg.script), '--port', "$pf", '--log', (Q $cfg.preflightLog))
try {
  $pfp = Start-Process -FilePath $cfg.python -ArgumentList $pfArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -PassThru -ErrorAction Stop
} catch {
  L "PREFLIGHT FAILED - could not start $($cfg.python): $_; the hub was NOT touched"
  DropLease
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}
$pfUp = $false
for ($i = 0; $i -lt 30 -and -not $pfUp; $i++) { Nap 2; $pfUp = Ok "http://127.0.0.1:$pf/api/platform" }
# /api/sessions walks every held transcript on a cold process (its cost/turn
# caches are empty); measured live at 30-32s against this hub's real history,
# right on top of the old 30s cap - the exact cause of "PREFLIGHT FAILED" on an
# otherwise healthy hub. Give it real headroom; a genuinely broken /api/sessions
# still fails preflight, just not until $sessTimeoutSec runs out.
$sessTimeoutSec = 90
if ($env:ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S) {
  try { $sessTimeoutSec = [int]$env:ENSEMBLE_PREFLIGHT_SESSIONS_TIMEOUT_S } catch {}
}
$pfOk = $pfUp -and (Ok "http://127.0.0.1:$pf/api/sessions?n=5" $sessTimeoutSec) -and (Ok "http://127.0.0.1:$pf/") -and (Ok "http://127.0.0.1:$pf/api/projects")
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
while ($left -gt 0) { $s = [int][math]::Min(10, [math]::Ceiling($left)); Nap $s; $left -= $s }

# --- 3. Stop the hub and start it again the way it was started. ---
# First the hub's last word on who is running and who is mid-turn. A hub from
# before this existed answers 404: the rooms of the plan are then resumed the
# old way in step 4.
try {
  $r = Post '/api/restart/snapshot' @{ lease = [string]$cfg.leaseId }
  L "snapshot before the stop: HTTP $($r.StatusCode) $($r.Content)"
} catch { L "no snapshot before the stop ($_): the one taken at the request, if any, is used" }
$useTask = $false
if (-not $cfg.noTask) {
  $task = Get-ScheduledTask -TaskName $cfg.taskName -ErrorAction SilentlyContinue
  if ($task) {
    $taskArgs = [string](@($task.Actions)[0].Arguments)
    $useTask = ($taskArgs -like '*dashboard.py*') -and ($taskArgs -match "--port\s+$port(\s|$)")
  }
}
if ($useTask) { L "through the scheduled task '$($cfg.taskName)'" } else { L "the hub process directly (not the scheduled task)" }
if ($useTask) {
  try { Stop-ScheduledTask -TaskName $cfg.taskName -ErrorAction Stop; L "stopped task" } catch { L "stop-task err: $_" }
  Start-Sleep -Seconds 3
}
$victims = @(HubProcs $port) + @(Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$cfg.hubPid)" -ErrorAction SilentlyContinue |
                                 Where-Object { $_.CommandLine -like '*dashboard.py*' })
foreach ($v in ($victims | Where-Object { $_ } | Sort-Object ProcessId -Unique)) {
  try { Stop-Process -Id $v.ProcessId -Force -ErrorAction Stop; L "stopped pid $($v.ProcessId)" } catch { L "stop pid $($v.ProcessId): $_" }
}
$free = $false
for ($i = 0; $i -lt 15 -and -not $free; $i++) {
  Nap 1
  $free = -not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
$hubArgs = @(Q $cfg.script) + @($cfg.args | ForEach-Object { Q $_ })
function StartDirect {
  try {
    Start-Process -FilePath $cfg.python -ArgumentList $hubArgs -WorkingDirectory $cfg.repo -WindowStyle Hidden -ErrorAction Stop | Out-Null
    L "started $($cfg.python) $($hubArgs -join ' ')"
  } catch { L "start err: $_" }
}
$up = $false
if ($useTask) {
  try { Start-ScheduledTask -TaskName $cfg.taskName -ErrorAction Stop; L "started task" } catch { L "start-task err: $_" }
  for ($i = 0; $i -lt 20 -and -not $up; $i++) { Nap 2; $up = Ok "$base/api/platform" }
  if (-not $up) { L "not up after the task; starting it directly the way it ran"; StartDirect }
} else {
  StartDirect
}
for ($i = 0; $i -lt 30 -and -not $up; $i++) { Nap 2; $up = Ok "$base/api/platform" }
if ($up) { RenewLease }
L "hub up: $up; code at $(Head) (was $head0)"
if (-not $up) {
  L "HUB STILL DOWN - start the '$($cfg.taskName)' scheduled task by hand"
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}

# --- 4. Bring the rooms back and tell the PO what to do next. ---
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
