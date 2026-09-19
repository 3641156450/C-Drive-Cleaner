# CleanCDrive-Launcher.ps1
# 运行库自检启动器（纯 PowerShell，不依赖任何 VC++ 运行库本身）。
# 作用：检测本机是否安装了 Microsoft Visual C++ Redistributable 2015-2022 (x64)；
#       若缺失，经用户同意后自动下载并管理员静默安装，再拉起 CleanCDrive.exe。
# 这是 exe 因缺运行库「双击无反应」时的最可靠兜底入口。
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$ExeCandidates = @(
    (Join-Path $PSScriptRoot 'dist\CleanCDrive.exe'),
    (Join-Path $PSScriptRoot 'CleanCDrive.exe'),
    (Join-Path $PSScriptRoot '..\dist\CleanCDrive.exe')
)

function Find-Exe {
    foreach ($p in $ExeCandidates) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-VcRedistX64 {
    $paths = @(
        'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64'
    )
    foreach ($p in $paths) {
        try {
            $v = Get-ItemProperty -Path $p -ErrorAction SilentlyContinue
            if ($v.Installed -eq 1) { return $true }
        } catch { }
    }
    $sys = $env:SystemRoot
    if ((Test-Path "$sys\System32\vcruntime140.dll") -and
        (Test-Path "$sys\System32\msvcp140.dll")) {
        return $true
    }
    return $false
}

$exe = Find-Exe
if (-not $exe) {
    [System.Windows.Forms.MessageBox]::Show(
        "未找到 CleanCDrive.exe。`n请确认本启动器与 dist\CleanCDrive.exe 位于同一目录（或把本启动器放到 exe 旁边）。",
        'C 盘清理工具', 'OK', 'Error') | Out-Null
    exit 1
}

if (Test-VcRedistX64) {
    Start-Process -FilePath $exe
    exit 0
}

$ans = [System.Windows.Forms.MessageBox]::Show(
    "本程序需要 Microsoft Visual C++ 运行库（2015-2022），当前电脑未安装。`n`n是否立即下载并安装？`n  · 需要联网`n  · 会弹出 Windows 管理员授权窗口，请点「是」`n`n安装完成后本程序会自动启动。",
    '运行库缺失 · C 盘清理工具', 'YesNo', 'Question')
if ($ans -ne 'Yes') {
    [System.Windows.Forms.MessageBox]::Show(
        '已取消。未安装运行库时程序可能无法启动。`n你可手动安装 Microsoft Visual C++ Redistributable 2015-2022 (x64) 后重试。',
        'C 盘清理工具', 'OK', 'Warning') | Out-Null
    exit 0
}

$tmp = $env:TEMP
$vc = Join-Path $tmp 'vc_redist.x64.exe'
$url = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
try {
    Write-Host '正在下载 Microsoft Visual C++ Redistributable…'
    Invoke-WebRequest -Uri $url -OutFile $vc -UseBasicParsing
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        '下载失败：请检查网络连接，或手动安装 Microsoft Visual C++ Redistributable 2015-2022 (x64) 后重试。',
        'C 盘清理工具', 'OK', 'Error') | Out-Null
    exit 1
}

try {
    Write-Host '正在安装运行库（会弹出管理员授权，请点「是」）…'
    Start-Process -FilePath $vc -ArgumentList '/quiet', '/norestart' -Verb RunAs -Wait
} catch {
    [System.Windows.Forms.MessageBox]::Show(
        '安装未完成：你可能取消了管理员授权。安装运行库后重新双击本启动器即可。',
        'C 盘清理工具', 'OK', 'Warning') | Out-Null
    exit 0
}

Start-Process -FilePath $exe
exit 0
