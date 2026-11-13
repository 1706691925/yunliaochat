# 局域网聊天室 - EXE 打包工具（PowerShell 版）
# 用法：右键此文件 -> 使用 PowerShell 运行；或在 PowerShell 中执行 .\构建EXE.ps1
# 若提示禁止运行脚本，先执行一次：Set-ExecutionPolicy -Scope Process RemoteSigned

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Line($t) { Write-Host $t }

Write-Line "============================================================"
Write-Line "  局域网聊天室 - EXE 一键打包工具"
Write-Line "  支持完全离线打包，内网机器无需联网"
Write-Line "============================================================"
Write-Line ""

# 1. 检测 Python
$py = $null
foreach ($c in @("python", "py", "python3")) {
    try {
        $v = & $c --version 2>&1
        if ($LASTEXITCODE -eq 0 -or $v) { $py = $c; Write-Line "[信息] 已检测到 $c -> $v"; break }
    } catch { }
}
if (-not $py) {
    Write-Line "[错误] 未检测到 Python。请先安装 Python 3.8 ~ 3.12，并勾选 Add Python to PATH。"
    Read-Host "按回车键退出"
    exit 1
}

# 2. 安装 PyInstaller（优先离线）
$hasPyi = & $py -c "import PyInstaller" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Line "[提示] 正在安装打包工具，优先使用 offline-wheels 离线包..."
    & $py -m pip install --no-index --find-links=offline-wheels --disable-pip-version-check --no-warn-script-location pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Line "[提示] 离线安装未成功，尝试联网安装..."
        & $py -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            Write-Line "[错误] PyInstaller 安装失败。"; Read-Host "按回车键退出"; exit 1
        }
    }
}
Write-Line "[信息] 打包工具就绪。"

# 3. 选择模式
Write-Line ""
Write-Line "请选择打包方式："
Write-Line "  [1] 单文件 EXE    推荐，便于分发，启动稍慢"
Write-Line "  [2] 目录版        启动更快，dist 下为文件夹"
Write-Line "  [3] 单文件无窗口  后台静默运行，不显示控制台"
$choice = Read-Host "请输入 1 / 2 / 3，直接回车默认 1"
if ([string]::IsNullOrWhiteSpace($choice)) { $choice = "1" }

$env:LC_APP_NAME = "EnterpriseLearnChat"
if ($choice -eq "2") { $env:LC_ONEFILE = "0"; $env:LC_CONSOLE = "1" }
elseif ($choice -eq "3") { $env:LC_ONEFILE = "1"; $env:LC_CONSOLE = "0" }
else { $env:LC_ONEFILE = "1"; $env:LC_CONSOLE = "1" }

Write-Line ""
Write-Line "[信息] 开始打包，请耐心等待 1 到 3 分钟..."
Write-Line ""

& $py -m PyInstaller --noconfirm --clean build.spec
if ($LASTEXITCODE -ne 0) {
    Write-Line "[错误] 打包失败，请查看上方日志。"; Read-Host "按回车键退出"; exit 1
}

Write-Line ""
Write-Line "============================================================"
Write-Line "  打包完成！"
if ($env:LC_ONEFILE -eq "1") { Write-Line "  输出文件： dist\EnterpriseLearnChat.exe" }
else { Write-Line "  输出目录： dist\EnterpriseLearnChat\EnterpriseLearnChat.exe" }
Write-Line ""
Write-Line "  使用步骤："
Write-Line "  1. 将生成的 EXE 复制到服务器任意目录，例如 D:\LearnChat"
Write-Line "  2. 双击运行，首次启动会自动创建 data 数据目录"
Write-Line "  3. 初始管理员密码写在 data\初始管理员密码.txt"
Write-Line "  4. 浏览器访问 http://本机IP:8080"
Write-Line "  5. 若 Windows 防火墙弹窗，请勾选 专用网络 允许访问"
Write-Line "============================================================"
Read-Host "按回车键退出"
