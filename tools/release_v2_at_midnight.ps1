param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$ReleaseMoment = [DateTimeOffset]::Parse('2026-09-01T00:00:00+08:00')
$Repository = 'C:\Users\Administrator\Documents\Codex\pig_catcher-2.0-dev'
$Package = 'D:\MaiBotArchives\pig_catcher\release_candidates\pig-catcher-2.0.13-final-hotfix2-20260901-004350'
$MaiBotRoot = 'C:\Users\Administrator\MaiBot'
$LivePlugin = 'C:\Users\Administrator\MaiBot\plugins\pig_catcher'
$LiveData = 'C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher'
$InternalPlugin = 'C:\Users\Administrator\MaiBot\plugins\pig_catcher_v2_internal'
$InternalData = 'C:\Users\Administrator\MaiBot\data\plugins\local.pig-catcher-v2-internal'
$MaiBotPython = 'C:\Users\Administrator\MaiBot\.venv\Scripts\python.exe'
$RepoPython = 'C:\Users\Administrator\Documents\Codex\pig_catcher-2.0-dev\.venv\Scripts\python.exe'
$Announcement = 'C:\Users\Administrator\Desktop\PiG Dream!抓猪派对! 2.0上线预告.md'
$AnnouncementImage = 'C:\Users\Administrator\Desktop\PiG Dream!抓猪派对! 2.0版本长图.jpg'
$ArchiveRoot = 'D:\MaiBotArchives\pig_catcher'
$RollbackParent = 'C:\Users\Administrator\MaiBot\release_rollback'

function Assert-ExactPath([string]$Path, [string]$Expected) {
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($resolved -ne $Expected) { throw "路径校验失败：$resolved != $Expected" }
}

function Stop-MaiBot {
    $targets = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq 'python.exe' -and
        $_.ExecutablePath -ieq $MaiBotPython -and
        $_.CommandLine -match '(^|[\\/\s\"])(bot\.py)([\s\"]|$)'
    }
    foreach ($target in $targets) {
        & taskkill.exe /PID $target.ProcessId /T /F | Out-Null
    }
    Start-Sleep -Seconds 2
    $remaining = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq 'python.exe' -and $_.ExecutablePath -ieq $MaiBotPython -and $_.CommandLine -match 'bot\.py'
    }
    if ($remaining) { throw 'MaiBot 进程未完全停止。' }
}

function Start-MaiBot {
    Start-Process -FilePath $MaiBotPython -ArgumentList 'bot.py' -WorkingDirectory $MaiBotRoot -WindowStyle Hidden
}

function Queue-Announcement([string]$GroupId) {
    $config = Join-Path $LivePlugin 'config.toml'
    & $RepoPython (Join-Path $Repository 'tools\queue_control_announcement.py') $config `
        --group $GroupId --platform 'qq-official' --content-file $Announcement --image-path $AnnouncementImage
    if ($LASTEXITCODE -ne 0) { throw "公告入队失败：$GroupId" }
    $consumed = $false
    foreach ($attempt in 1..18) {
        Start-Sleep -Seconds 3
        $status = & $RepoPython (Join-Path $Repository 'tools\queue_control_announcement.py') $config --status
        if ($status.Trim() -eq 'false') { $consumed = $true; break }
    }
    if (-not $consumed) { throw "公告触发未被消费：$GroupId" }
}

$required = @($Repository,$Package,$MaiBotRoot,$LivePlugin,$LiveData,$MaiBotPython,$RepoPython,$Announcement,$AnnouncementImage)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) { throw "缺少发布输入：$path" }
}
Assert-ExactPath $Repository $Repository
Assert-ExactPath $Package $Package
Assert-ExactPath $LivePlugin $LivePlugin
Assert-ExactPath $LiveData $LiveData

& $RepoPython -c "import sys; from pathlib import Path; sys.path.insert(0,r'$Repository'); from tools.build_production_release import verify_production_package; print(verify_production_package(Path(r'$Package'))['inventory_sha256'])"
if ($LASTEXITCODE -ne 0) { throw '最终发布包校验失败。' }

# This script performs a destructive clean launch. Refuse a second run once the
# v2 schema has reached the live database, even if the timestamp condition is met.
$liveDatabase = Join-Path $LiveData 'pig_catcher.sqlite3'
$liveSchema = & $RepoPython -c "import sqlite3; c=sqlite3.connect(r'$liveDatabase'); print(c.execute('PRAGMA user_version').fetchone()[0]); c.close()"
if ($LASTEXITCODE -ne 0) { throw '无法读取正式数据库版本，拒绝继续。' }
if ([int]$liveSchema -ge 56) {
    throw "2.0 已经上线（正式库 schema=$liveSchema），拒绝再次执行删档发布。"
}
if ($PreflightOnly) {
    Write-Output 'PREFLIGHT_OK'
    exit 0
}

$now = [DateTimeOffset]::Now
if ($now -lt $ReleaseMoment) { throw "尚未到正式发布时间：$now" }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$staging = Join-Path $ArchiveRoot "release_staging\fresh-data-2.0.13-live-$stamp"
$rollback = Join-Path $RollbackParent $stamp
$logDir = Join-Path $ArchiveRoot 'release_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$resultLog = Join-Path $logDir "release-2.0.13-$stamp.log"
"[$(Get-Date -Format o)] RELEASE_START" | Set-Content -LiteralPath $resultLog -Encoding utf8
$stopped = $false
$switched = $false

try {
    & $RepoPython (Join-Path $Repository 'tools\prepare_v2_launch_data.py') --package $Package --source-data $LiveData --output $staging | Add-Content -LiteralPath $resultLog -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw '正式切换前的新库准备失败。' }

    Stop-MaiBot
    $stopped = $true
    New-Item -ItemType Directory -Path $rollback -Force | Out-Null
    Move-Item -LiteralPath $LivePlugin -Destination (Join-Path $rollback 'formal-plugin-1.x')
    Move-Item -LiteralPath $LiveData -Destination (Join-Path $rollback 'formal-data-1.x')
    if (Test-Path -LiteralPath $InternalPlugin) {
        Move-Item -LiteralPath $InternalPlugin -Destination (Join-Path $rollback 'internal-plugin-2.0')
    }
    if (Test-Path -LiteralPath $InternalData) {
        Move-Item -LiteralPath $InternalData -Destination (Join-Path $rollback 'internal-data-2.0')
    }
    $switched = $true
    Copy-Item -LiteralPath $Package -Destination $LivePlugin -Recurse
    Copy-Item -LiteralPath $staging -Destination $LiveData -Recurse
    $gitMetadata = Join-Path $rollback 'formal-plugin-1.x\.git'
    if (Test-Path -LiteralPath $gitMetadata -PathType Container) {
        Copy-Item -LiteralPath $gitMetadata -Destination (Join-Path $LivePlugin '.git') -Recurse
    }

    & $RepoPython (Join-Path $Repository 'tools\verify_live_release.py') (Join-Path $LiveData 'pig_catcher.sqlite3') --schema 56 | Add-Content -LiteralPath $resultLog -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw '上线数据库校验失败。' }

    Start-MaiBot
    Start-Sleep -Seconds 20
    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -ieq 'python.exe' -and $_.ExecutablePath -ieq $MaiBotPython -and $_.CommandLine -match 'bot\.py'
    }
    if (-not $running) { throw '新版 MaiBot 启动后进程不存在。' }
    Queue-Announcement '5E5854406D0297D6FEAE696A13E3A339'
    Queue-Announcement '9EA2810F378FBD7DC3219C56CEAB3520'
    "[$(Get-Date -Format o)] RELEASE_OK rollback=$rollback staging=$staging" | Add-Content -LiteralPath $resultLog -Encoding utf8
    Write-Output "RELEASE_OK rollback=$rollback log=$resultLog"
}
catch {
    $failure = $_
    "[$(Get-Date -Format o)] RELEASE_FAILED $failure" | Add-Content -LiteralPath $resultLog -Encoding utf8
    if ($stopped) { Stop-MaiBot }
    if ($switched) {
        if (Test-Path -LiteralPath $LivePlugin) { Move-Item -LiteralPath $LivePlugin -Destination (Join-Path $rollback 'failed-live-plugin') }
        if (Test-Path -LiteralPath $LiveData) { Move-Item -LiteralPath $LiveData -Destination (Join-Path $rollback 'failed-live-data') }
        $oldPlugin = Join-Path $rollback 'formal-plugin-1.x'
        $oldData = Join-Path $rollback 'formal-data-1.x'
        if (Test-Path -LiteralPath $oldPlugin) { Move-Item -LiteralPath $oldPlugin -Destination $LivePlugin }
        if (Test-Path -LiteralPath $oldData) { Move-Item -LiteralPath $oldData -Destination $LiveData }
        $oldInternalPlugin = Join-Path $rollback 'internal-plugin-2.0'
        $oldInternalData = Join-Path $rollback 'internal-data-2.0'
        if (Test-Path -LiteralPath $oldInternalPlugin) { Move-Item -LiteralPath $oldInternalPlugin -Destination $InternalPlugin }
        if (Test-Path -LiteralPath $oldInternalData) { Move-Item -LiteralPath $oldInternalData -Destination $InternalData }
    }
    if ($stopped) { Start-MaiBot }
    throw $failure
}
