# 注册开机计划任务:电脑重启后「不管有没有人登录」都自动拉起新闻站(看门狗 -> run.py -> server + bot + 隧道)。
#
# 为什么:现在只有 Startup 快捷方式,要登录后才运行。2026-09-20 21:55 开机、23:39 才登录,
# 公网 /news 停了 1 小时 44 分;近 60 天非正常关机 4 次。
#
# 用法:右键 PowerShell「以管理员身份运行」,执行
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\新闻\tools\install_boot_task.ps1"
# 会弹窗要你输入当前 Windows 账户的密码(计划任务「不管是否登录都运行」必须存密码,由 Windows 加密保存)。
#
# 说明:
# - 看门狗有单实例锁(端口 8912):登录后 Startup 快捷方式再启动一份会自动退出,不会重复运行。
# - 没人登录时 v2rayN(VPN) 不会启动:网站、X、DeepSeek 照常;Telegram 推送要等登录、VPN 起来后才恢复。
# - goldhot(黄金板块)不在这个任务里,仍是登录后才启动。
# - 撤销:Unregister-ScheduledTask -TaskName AIHOT-NewsWatchdog-Boot -Confirm:$false

$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "请用「以管理员身份运行」的 PowerShell 执行本脚本。" -ForegroundColor Red
    exit 1
}

$py  = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
$dir = Split-Path -Parent $PSScriptRoot
$wd  = Join-Path $dir "watchdog.py"
foreach ($p in @($py, $wd)) { if (-not (Test-Path $p)) { throw "找不到 $p" } }

$action  = New-ScheduledTaskAction -Execute $py -Argument ('"{0}" --loop' -f $wd) -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT1M'                       # 开机后等 1 分钟,让网络先起来
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $user -Message "输入 $user 的 Windows 登录密码(计划任务无人登录时运行需要)"
Register-ScheduledTask -TaskName 'AIHOT-NewsWatchdog-Boot' -Description 'AIHOT 新闻站:开机即启动看门狗(不管是否登录)' `
    -Action $action -Trigger $trigger -Settings $settings `
    -User $cred.UserName -Password $cred.GetNetworkCredential().Password -RunLevel Highest -Force | Out-Null

$t = Get-ScheduledTask -TaskName 'AIHOT-NewsWatchdog-Boot'
Write-Host ("已注册:{0}  状态:{1}  登录类型:{2}" -f $t.TaskName, $t.State, $t.Principal.LogonType) -ForegroundColor Green
Write-Host "下次重启后无需登录即会自动运行。现在不需要手动启动(看门狗已在运行)。"
