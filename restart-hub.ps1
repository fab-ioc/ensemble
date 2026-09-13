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
#   3. Stop the hub (through its scheduled task when that is how it runs) and
#      start it again the same way; wait until it answers.
#   4. Resume the rooms in the plan and type the PO what to do next.
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
function Ok($u) { try { $r = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 30; return ($r.StatusCode -eq 200) } catch { return $false } }
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
for ($i = 0; $i -lt 30 -and -not $pfUp; $i++) { Start-Sleep -Seconds 2; $pfUp = Ok "http://127.0.0.1:$pf/api/platform" }
$pfOk = $pfUp -and (Ok "http://127.0.0.1:$pf/api/sessions?n=5") -and (Ok "http://127.0.0.1:$pf/") -and (Ok "http://127.0.0.1:$pf/api/projects")
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
if ($left -gt 0) { Start-Sleep -Seconds ([int][math]::Ceiling($left)) }

# --- 3. Stop the hub and start it again the way it was started. ---
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
  Start-Sleep -Seconds 1
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
  for ($i = 0; $i -lt 20 -and -not $up; $i++) { Start-Sleep -Seconds 2; $up = Ok "$base/api/platform" }
  if (-not $up) { L "not up after the task; starting it directly the way it ran"; StartDirect }
} else {
  StartDirect
}
for ($i = 0; $i -lt 30 -and -not $up; $i++) { Start-Sleep -Seconds 2; $up = Ok "$base/api/platform" }
L "hub up: $up; code at $(Head) (was $head0)"
if (-not $up) {
  L "HUB STILL DOWN - start the '$($cfg.taskName)' scheduled task by hand"
  Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
  exit 1
}

# --- 4. Resume the rooms and tell the PO what to do next. ---
Start-Sleep -Seconds 4
foreach ($room in @($cfg.resumeRooms)) {
  if (-not $room) { continue }
  try { $r = Post '/api/room/resume' @{ roomId = $room }; L "resume ${room}: HTTP $($r.StatusCode)" } catch { L "resume $room failed: $_" }
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
      Post '/api/pty/input' @{ id = $pty; data = [string]$cfg.wakeText } | Out-Null
      Start-Sleep -Milliseconds 500
      Post '/api/pty/input' @{ id = $pty; data = "`r" } | Out-Null
      L "woke the PO"
    } catch { L "wake failed: $_" }
  }
}
L "=== done ==="
Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
