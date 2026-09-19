#Requires -Version 5.1
<#
  ============================================================================
  C 盘安全清理工具  (Safe C: Drive Cleaner)
  说明：
    - 仅清理「白名单」内的安全临时/缓存目录，绝不直接触碰系统关键目录。
    - 删除前展示完整待清理清单、各项大小、预估释放空间，并标注受保护目录。
    - 必须输入 YES 明确确认后才执行删除。
    - 内置结构性保护：删除对象只能是扫描阶段在白名单内收集到的文件，
      任何解析后落在白名单之外的路径都会被拒绝（双层防护）。
  用法：
    .\CleanCDrive.bat            # 交互式预览 + 确认后删除
    .\CleanCDrive.ps1 -DryRun    # 仅预览，不删除
    .\CleanCDrive.ps1 -SkipRecycleBin   # 不处理回收站
  ============================================================================
#>

[CmdletBinding()]
param(
    [string]$ConfigPath = "",
    [switch]$DryRun,
    [switch]$SkipRecycleBin,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
try { $Host.UI.RawUI.WindowTitle = "C 盘安全清理工具" } catch { }

$PSScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptDir 'cleanup_config.ini' }
$LogPath = Join-Path $PSScriptDir ("cleanup_report_" + (Get-Date -Format 'yyyyMMdd_HHmmss') + ".log")
$DriveLetter = 'C'

# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
function Format-Size($bytes) {
    if ($null -eq $bytes) { return "0 B" }
    [int64]$b = $bytes
    if ($b -ge 1GB) { return ("{0:N2} GB" -f ($b / 1GB)) }
    if ($b -ge 1MB) { return ("{0:N2} MB" -f ($b / 1MB)) }
    if ($b -ge 1KB) { return ("{0:N2} KB" -f ($b / 1KB)) }
    return ("{0} B" -f $b)
}

function Get-DiskFree([char]$Drive) {
    $d = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$Drive`:'" -ErrorAction SilentlyContinue
    if ($d) { return [PSCustomObject]@{Free = [int64]$d.FreeSpace; Total = [int64]$d.Size } }
    return [PSCustomObject]@{Free = [int64]0; Total = [int64]0 }
}

# 受保护的系统关键目录（仅用于展示；删除逻辑在结构上已禁止触及这些位置）
$ProtectedRoots = @(
    "C:\Windows",
    "C:\Program Files",
    "C:\Program Files (x86)",
    "C:\Users",
    "C:\ProgramData",
    "C:\`$Recycle.Bin",
    "C:\System Volume Information",
    "启动分区 / EFI / 保留分区及 C: 根目录下的直接文件"
)

# 默认安全扫描根（白名单）。只有这里面的文件才会被收集与删除。
$DefaultSafeRoots = @(
    $env:TEMP,
    "C:\Windows\Temp",
    "C:\Windows\SoftwareDistribution\Download",
    "C:\Windows\Prefetch",
    "$env:LOCALAPPDATA\Microsoft\Windows\INetCache",
    "$env:LOCALAPPDATA\Microsoft\Windows\Temporary Internet Files",
    "$env:LOCALAPPDATA\Microsoft\Windows\Explorer"
)

# ----------------------------------------------------------------------------
# 读取配置文件（INI 简易解析）
# ----------------------------------------------------------------------------
function Read-Config($path) {
    $cfg = [PSCustomObject]@{
        RetainPaths       = [System.Collections.Generic.List[string]]::new()
        RetainPatterns    = [System.Collections.Generic.List[string]]::new()
        AddRoots          = [System.Collections.Generic.List[string]]::new()
        ExcludeRoots      = [System.Collections.Generic.List[string]]::new()
        RetainFileAgeDays = 0
        ExcludeApps       = [System.Collections.Generic.List[string]]::new()
        ExcludePaths      = [System.Collections.Generic.List[string]]::new()
        ExcludePatterns   = [System.Collections.Generic.List[string]]::new()
        AppCacheEnabled   = $true
    }
    if (-not (Test-Path $path)) {
        Write-Warning "未找到配置文件: $path （将使用默认设置，不保留任何额外内容）"
        return $cfg
    }
    $section = ""
    foreach ($line in (Get-Content -Path $path -Encoding UTF8)) {
        $l = $line.Trim()
        if ($l -eq '' -or $l.StartsWith(';') -or $l.StartsWith('#')) { continue }
        if ($l -match '^\[(.+)\]$') { $section = $matches[1].Trim(); continue }
        $key = ''; $val = ''
        if ($l -match '^(.*?)=(.*)$') { $key = $matches[1].Trim(); $val = $matches[2].Trim() }
        else { $key = ''; $val = $l }   # 无等号的行视为裸路径
        switch ($section) {
            'Retain' {
                if ($key -like 'Pattern*') { if ($val) { $cfg.RetainPatterns.Add($val) } }
                else { if ($val) { $cfg.RetainPaths.Add($val) } }
            }
            'SafeRoots' {
                if ($key -like 'Exclude*') { if ($val) { $cfg.ExcludeRoots.Add($val) } }
                else { if ($val) { $cfg.AddRoots.Add($val) } }
            }
            'Options' {
                if ($key -like 'RetainFileAgeDays*') { [void][int]::TryParse($val, [ref]$cfg.RetainFileAgeDays) }
            }
        }
    }
    return $cfg
}

function Expand-Env($s) {
    if ($null -eq $s) { return '' }
    return $ExecutionContext.InvokeCommand.ExpandString($s)
}

# 保留过滤器：命中则返回 $true（跳过，不删除）
function Test-Retain($fullPath, $name, $cfg) {
    foreach ($p in $cfg.RetainPaths) {
        $pp = (Expand-Env $p).TrimEnd('\')
        if ($fullPath -eq $pp -or $fullPath -like "$pp\*") { return $true }
    }
    foreach ($pat in $cfg.RetainPatterns) {
        if ($name -like $pat) { return $true }
    }
    if ($cfg.RetainFileAgeDays -gt 0) {
        try {
            $age = (Get-Date) - (Get-Item $fullPath -ErrorAction SilentlyContinue).LastWriteTime
            if ($age.TotalDays -lt $cfg.RetainFileAgeDays) { return $true }
        } catch { }
    }
    return $false
}

# ----------------------------------------------------------------------------
# 构建有效安全根
# ----------------------------------------------------------------------------
$cfg = Read-Config $ConfigPath

$SafeRoots = [System.Collections.Generic.List[string]]::new()
foreach ($r in $DefaultSafeRoots) {
    if (-not $r) { continue }
    $exp = Expand-Env $r
    if (Test-Path $exp) {
        $norm = (Resolve-Path $exp).Path.TrimEnd('\')
        if ($cfg.ExcludeRoots -notcontains $norm) { if (-not $SafeRoots.Contains($norm)) { $SafeRoots.Add($norm) } }
    }
}
foreach ($r in $cfg.AddRoots) {
    $exp = Expand-Env $r
    if (Test-Path $exp) {
        $norm = (Resolve-Path $exp).Path.TrimEnd('\')
        if (-not $SafeRoots.Contains($norm)) { $SafeRoots.Add($norm) }
    }
}

# ----------------------------------------------------------------------------
# 应用软件缓存定义（白名单：仅列出的具体缓存子目录，且按 Patterns 过滤）
# 安全原则：Root 必须是「明确的缓存子目录」（如 Cache / Code Cache / logs），
#           绝不直接指向软件主目录；Patterns 限定可删文件类型，默认缓存目录为 *（全部）。
# ----------------------------------------------------------------------------
$AppCacheDefs = @(
    # ---- 浏览器缓存 ----
    @{Name='Chrome 缓存';        Root='$env:LOCALAPPDATA\Google\Chrome\User Data\*\Cache';                 Patterns=@('*')},
    @{Name='Chrome 代码缓存';    Root='$env:LOCALAPPDATA\Google\Chrome\User Data\*\Code Cache';            Patterns=@('*')},
    @{Name='Chrome GPU缓存';     Root='$env:LOCALAPPDATA\Google\Chrome\User Data\*\GPUCache';              Patterns=@('*')},
    @{Name='Edge 缓存';          Root='$env:LOCALAPPDATA\Microsoft\Edge\User Data\*\Cache';                Patterns=@('*')},
    @{Name='Edge 代码缓存';      Root='$env:LOCALAPPDATA\Microsoft\Edge\User Data\*\Code Cache';           Patterns=@('*')},
    @{Name='Edge GPU缓存';       Root='$env:LOCALAPPDATA\Microsoft\Edge\User Data\*\GPUCache';             Patterns=@('*')},
    @{Name='Firefox 缓存';       Root='$env:LOCALAPPDATA\Mozilla\Firefox\Profiles\*\cache2';               Patterns=@('*')},
    @{Name='Firefox 缓存旧';     Root='$env:LOCALAPPDATA\Mozilla\Firefox\Profiles\*\cache';                Patterns=@('*')},
    @{Name='Brave 缓存';         Root='$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data\*\Cache';   Patterns=@('*')},
    @{Name='Brave 代码缓存';     Root='$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data\*\Code Cache'; Patterns=@('*')},
    @{Name='Opera 缓存';         Root='$env:LOCALAPPDATA\Opera Software\Opera\Cache';                      Patterns=@('*')},
    # ---- 即时通讯 ----
    @{Name='Discord 缓存';       Root='$env:LOCALAPPDATA\Discord\Cache';                                   Patterns=@('*')},
    @{Name='Slack 缓存';         Root='$env:LOCALAPPDATA\Slack\Cache';                                    Patterns=@('*')},
    @{Name='Slack 日志';         Root='$env:LOCALAPPDATA\Slack\logs';                                     Patterns=@('*.log','*.txt','*.old')},
    @{Name='Teams 缓存';         Root='$env:LOCALAPPDATA\Microsoft\Teams\Cache';                          Patterns=@('*')},
    @{Name='Teams 代码缓存';     Root='$env:LOCALAPPDATA\Microsoft\Teams\Code Cache';                     Patterns=@('*')},
    @{Name='Teams GPU缓存';      Root='$env:LOCALAPPDATA\Microsoft\Teams\GPUCache';                        Patterns=@('*')},
    @{Name='Teams 临时';         Root='$env:LOCALAPPDATA\Microsoft\Teams\tmp';                            Patterns=@('*')},
    @{Name='Teams 日志';         Root='$env:LOCALAPPDATA\Microsoft\Teams\logs';                           Patterns=@('*.log','*.txt','*.old')},
    @{Name='Telegram 缓存';      Root='$env:LOCALAPPDATA\Telegram Desktop\tdata\cache';                   Patterns=@('*')},
    @{Name='WhatsApp 缓存';      Root='$env:LOCALAPPDATA\WhatsApp\Cache';                                Patterns=@('*')},
    @{Name='微信 缓存';          Root='$env:USERPROFILE\Documents\WeChat Files\*\FileStorage\Cache';      Patterns=@('*')},
    @{Name='QQ 临时';            Root='$env:LOCALAPPDATA\Tencent\QQ\Temp';                                Patterns=@('*')},
    # ---- 开发工具 ----
    @{Name='npm 缓存';           Root='$env:LOCALAPPDATA\npm-cache';                                      Patterns=@('*')},
    @{Name='pip 缓存';           Root='$env:LOCALAPPDATA\pip\Cache';                                      Patterns=@('*')},
    @{Name='Yarn 缓存';          Root='$env:LOCALAPPDATA\Yarn\Cache';                                     Patterns=@('*')},
    @{Name='JetBrains 日志';     Root='$env:LOCALAPPDATA\JetBrains\*\log';                               Patterns=@('*.log','*.txt')},
    # ---- 媒体 / 游戏 ----
    @{Name='Spotify 缓存';       Root='$env:LOCALAPPDATA\Spotify\Data';                                   Patterns=@('*')},
    @{Name='Spotify 封面缓存';   Root='$env:LOCALAPPDATA\Spotify\Storage';                               Patterns=@('*')},
    @{Name='Steam 下载残留';     Root='$env:LOCALAPPDATA\Steam\steamapps\downloading';                     Patterns=@('*')},
    @{Name='Steam 应用缓存';     Root='$env:LOCALAPPDATA\Steam\appcache';                                 Patterns=@('*')},
    @{Name='NVIDIA 着色器缓存';  Root='$env:LOCALAPPDATA\NVIDIA\GLCache';                                Patterns=@('*')},
    @{Name='Adobe 媒体缓存';     Root='$env:LOCALAPPDATA\Adobe\Common\Media Cache Files';                 Patterns=@('*')},
    @{Name='Adobe 媒体缓存旧';   Root='$env:LOCALAPPDATA\Adobe\Common\Media Cache';                       Patterns=@('*')},
    # ---- 系统更新分发优化 ----
    @{Name='传递优化缓存';       Root='C:\ProgramData\Microsoft\Windows\DeliveryOptimization\Cache';       Patterns=@('*')}
)

# 解析应用缓存扫描根（处理通配符目录、排除项），合并进删除白名单
$AppCacheRoots = [System.Collections.Generic.List[object]]::new()
if (-not $SkipAppCache -and $cfg.AppCacheEnabled) {
    foreach ($d in $AppCacheDefs) {
        if ($cfg.ExcludeApps -contains $d.Name) { continue }
        $exp = Expand-Env $d.Root
        try { $dirs = @(Get-ChildItem -Path $exp -Directory -Force -ErrorAction SilentlyContinue) } catch { $dirs = @() }
        foreach ($dir in $dirs) {
            $dp = $dir.FullName.TrimEnd('\')
            $skip = $false
            foreach ($xp in $cfg.ExcludePaths) {
                $xpe = (Expand-Env $xp).TrimEnd('\')
                if ($dp -eq $xpe -or $dp -like "$xpe\*") { $skip = $true; break }
            }
            if ($skip) { continue }
            $AppCacheRoots.Add([PSCustomObject]@{Name = "应用缓存: $($d.Name)"; Root = $dp; Patterns = $d.Patterns })
        }
    }
}

# 合并所有允许删除的安全根（系统临时 + 应用缓存），用于删除阶段的结构性越界校验
$AllAllowedRoots = [System.Collections.Generic.List[string]]::new()
foreach ($r in $SafeRoots) { if (-not $AllAllowedRoots.Contains($r)) { $AllAllowedRoots.Add($r) } }
foreach ($r in $AppCacheRoots) { if (-not $AllAllowedRoots.Contains($r.Root)) { $AllAllowedRoots.Add($r.Root) } }

# ----------------------------------------------------------------------------
# 扫描单个安全根
# ----------------------------------------------------------------------------
function Scan-Root($root, $cfg, [ref]$skippedRef, $patterns = $null) {
    $res = [PSCustomObject]@{Name = $root; Files = [System.Collections.Generic.List[object]]::new(); TotalSize = [int64]0; Count = 0 }
    $rootNorm = $root + '\'
    try {
        Get-ChildItem -Path $root -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $fp = $_.FullName
            # 结构性安全冗余：只接受确实位于该安全根内的文件
            if ($fp -notlike "$rootNorm*") { return }
            # 应用缓存：仅收集匹配安全模式的文件（如 *.log、* 等）
            if ($patterns) {
                $match = $false
                foreach ($p in $patterns) { if ($_.Name -like $p) { $match = $true; break } }
                if (-not $match) { return }
            }
            if (Test-Retain $fp $_.Name $cfg) { $skippedRef.Value++; return }
            if ($cfg.ExcludePatterns) {
                foreach ($ep in $cfg.ExcludePatterns) { if ($_.Name -like $ep) { $skippedRef.Value++; return } }
            }
            $res.Files.Add([PSCustomObject]@{Path = $fp; Size = [int64]$_.Length })
            $res.TotalSize += $_.Length
            $res.Count++
        }
    } catch { }
    return $res
}

# ----------------------------------------------------------------------------
# 回收站信息（COM 估算）
# ----------------------------------------------------------------------------
function Get-ShellItemSize($item) {
    [int64]$sz = 0
    try { $sz = [int64]$item.Size } catch { $sz = 0 }
    if ($sz -eq 0) {
        try {
            $fld = $item.GetFolder()
            if ($fld) { $fld.Items() | ForEach-Object { $sz += (Get-ShellItemSize $_) } }
        } catch { }
    }
    return $sz
}
function Get-RecycleBinInfo([char]$Drive) {
    try {
        $shell = New-Object -ComObject Shell.Application
        $bin = $shell.NameSpace(0x0a)   # 0x0a = Recycle Bin
        [int64]$total = 0; [int]$count = 0
        $bin.Items() | ForEach-Object { $total += (Get-ShellItemSize $_); $count++ }
        return [PSCustomObject]@{Size = $total; Count = $count; Available = $true }
    } catch {
        return [PSCustomObject]@{Size = 0; Count = 0; Available = $false; Error = $_.Exception.Message }
    }
}

# ----------------------------------------------------------------------------
# 扫描阶段
# ----------------------------------------------------------------------------
$before = Get-DiskFree $DriveLetter
$categories = @()
$skipped = 0

if (-not $AppCacheOnly) {
    foreach ($root in $SafeRoots) {
        $categories += Scan-Root $root $cfg ([ref]$skipped)
    }
}
foreach ($ac in $AppCacheRoots) {
    $categories += Scan-Root $ac.Root $cfg ([ref]$skipped) $ac.Patterns
}

$recycle = $null
if (-not $SkipRecycleBin) {
    $recycle = Get-RecycleBinInfo $DriveLetter
}

[long]$totalFiles = 0; [int64]$totalSize = 0
foreach ($c in $categories) { $totalFiles += $c.Count; $totalSize += $c.TotalSize }
if ($recycle -and $recycle.Available) { $totalFiles += $recycle.Count; $totalSize += $recycle.Size }

# ----------------------------------------------------------------------------
# 生成预览报告
# ----------------------------------------------------------------------------
$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("================================================================")
$lines.Add("            C 盘安全清理 — 待清理清单预览")
$lines.Add("================================================================")
$lines.Add("扫描时间 : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
$lines.Add("清理盘符 : $DriveLetter`:")
$lines.Add("")
$lines.Add("【受保护的系统关键目录（绝不会删除，仅展示）】")
foreach ($p in $ProtectedRoots) { $lines.Add("  - " + $p) }
$lines.Add("")
$lines.Add("【可安全清理的扫描根（白名单）】")
foreach ($r in $SafeRoots) { $lines.Add("  - " + $r) }
$lines.Add("")
$lines.Add(("保留规则：保留路径 {0} 项，保留模式 {1} 项，仅删超过 {2} 天的文件" -f $cfg.RetainPaths.Count, $cfg.RetainPatterns.Count, $cfg.RetainFileAgeDays))
$lines.Add(("扫描时已跳过保留项：{0} 个" -f $skipped))
$lines.Add("")
$lines.Add("【分类明细（预估）】")
$idx = 1
foreach ($c in $categories) {
    $lines.Add(("  {0}. {1}" -f $idx, $c.Name))
    $lines.Add(("     文件数: {0,-8} 预估大小: {1}" -f $c.Count, (Format-Size $c.TotalSize)))
    $idx++
}
if ($recycle -and $recycle.Available) {
    $lines.Add(("  {0}. 回收站 (C:)" -f $idx))
    $lines.Add(("     项目数: {0,-8} 预估大小: {1}  (注：回收站为文件夹递归估算，可能不完全精确)" -f $recycle.Count, (Format-Size $recycle.Size)))
}
$lines.Add("")
$lines.Add(("【合计】文件/项目数: {0}   预估可释放: {1}" -f $totalFiles, (Format-Size $totalSize)))
$lines.Add(("清理前 C: 盘可用空间: {0} / 总容量 {1}" -f (Format-Size $before.Free), (Format-Size $before.Total)))
$lines.Add("================================================================")

# 仅 DryRun 模式：输出预览后退出，不删除
if ($DryRun) {
    $lines.Add("[DryRun] 仅预览模式，未执行任何删除操作。")
    $lines | ForEach-Object { Write-Host $_ }
    $lines | Out-File -FilePath $LogPath -Encoding UTF8
    if (-not $Quiet) { Write-Host ("`n预览报告已保存: " + $LogPath) }
    return
}

# ----------------------------------------------------------------------------
# 确认门
# ----------------------------------------------------------------------------
Write-Host ""
$confirm = Read-Host "⚠ 请输入 YES（全大写）以确认执行删除；输入其他任意内容取消"
if ($confirm -cne 'YES') {
    $lines.Add("用户未确认，操作已取消。未删除任何文件。")
    $lines | ForEach-Object { Write-Host $_ }
    $lines | Out-File -FilePath $LogPath -Encoding UTF8
    Write-Host ("`n已取消。报告已保存: " + $LogPath)
    return
}

# ----------------------------------------------------------------------------
# 执行删除（仅处理白名单内已收集的文件）
# ----------------------------------------------------------------------------
$lines.Add("")
$lines.Add(">>> 开始执行删除...")

[long]$deletedCount = 0; [int64]$deletedSize = 0; [long]$failCount = 0
foreach ($c in $categories) {
    [long]$cc = 0; [int64]$cs = 0; [long]$cf = 0
    foreach ($f in $c.Files) {
        # 二次防护：删除前再次确认路径位于某个安全根内
        $ok = $false
        foreach ($sr in $SafeRoots) { if ($f.Path -like ($sr + '\*')) { $ok = $true; break } }
        if (-not $ok) { $cf++; continue }
        try {
            Remove-Item -Path $f.Path -Force -ErrorAction Stop
            $cc++; $cs += $f.Size
        } catch {
            $cf++
        }
    }
    $deletedCount += $cc; $deletedSize += $cs; $failCount += $cf
    $c | Add-Member -NotePropertyName DeletedCount -NotePropertyValue $cc -Force
    $c | Add-Member -NotePropertyName DeletedSize -NotePropertyValue $cs -Force
    $lines.Add(("  清理 {0}: 删除 {1} 个文件, 释放 {2}, 失败 {3}" -f $c.Name, $cc, (Format-Size $cs), $cf))
}

# 清理空目录（仅安全根内，且不为安全根本体；删除前再次校验父目录在白名单内）
foreach ($root in $SafeRoots) {
    try {
        $dirs = Get-ChildItem -Path $root -Recurse -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.FullName.TrimEnd('\') -ne $root }
        $dirs | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
            $dp = $_.FullName
            $ok = $false
            foreach ($sr in $SafeRoots) { if ($dp -like ($sr + '\*')) { $ok = $true; break } }
            if (-not $ok) { return }
            try {
                if ((Get-ChildItem -Path $dp -Force -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
                    Remove-Item -Path $dp -Force -ErrorAction SilentlyContinue
                }
            } catch { }
        }
    } catch { }
}

# 回收站
if ($recycle -and $recycle.Available) {
    try {
        Clear-RecycleBin -DriveLetter $DriveLetter -Force -ErrorAction SilentlyContinue
        $lines.Add(("  清空回收站 (C:): 完成（{0} 项）" -f $recycle.Count))
        $deletedCount += $recycle.Count; $deletedSize += $recycle.Size
    } catch {
        $lines.Add(("  清空回收站失败: " + $_.Exception.Message))
    }
}

# ----------------------------------------------------------------------------
# 完成后对比报告
# ----------------------------------------------------------------------------
$after = Get-DiskFree $DriveLetter
[long]$realFreed = $after.Free - $before.Free

$lines.Add("")
$lines.Add("==================== 清理完成报告 ====================")
$lines.Add(("清理前 C: 可用空间 : {0}" -f (Format-Size $before.Free)))
$lines.Add(("清理后 C: 可用空间 : {0}" -f (Format-Size $after.Free)))
$lines.Add(("实际可用空间变化   : {0}" -f (Format-Size $realFreed)))
$lines.Add(("理论释放（删除字节）: {0}  （成功 {1} 个，失败 {2} 个）" -f (Format-Size $deletedSize), $deletedCount, $failCount))
$lines.Add("----------------------------------------------------")
$lines.Add("分类明细（实际删除）：")
[long]$anyDel = 0
foreach ($c in $categories) {
    if ($c.DeletedCount -gt 0) {
        $lines.Add(("  - {0}: {1}  ({2} 个文件)" -f $c.Name, (Format-Size $c.DeletedSize), $c.DeletedCount))
        $anyDel++
    }
}
if ($anyDel -eq 0) { $lines.Add("  （本次没有任何文件被删除，详见上方各分类的失败计数）") }
if ($recycle -and $recycle.Available) { $lines.Add(("  - 回收站 (C:): " + (Format-Size $recycle.Size))) }
$lines.Add("=====================================================")
$lines.Add("报告已保存: " + $LogPath)

$lines | ForEach-Object { Write-Host $_ }
$lines | Out-File -FilePath $LogPath -Encoding UTF8
Write-Host ("`n完成。报告已保存: " + $LogPath)
