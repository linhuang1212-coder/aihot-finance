# 注册开机计划任务:电脑重启后「不管有没有人登录」都自动拉起新闻站(看门狗 -> run.py -> server + bot + 隧道)。
#
# 为什么:原来只有 Startup 快捷方式,要登录后才运行。2026-09-20 21:55 开机、23:39 才登录,
# 公网 /news 停了 1 小时 44 分;近 60 天非正常关机 4 次。
#
# 用法:以管理员身份运行(不需要输入密码)
#   powershell -ExecutionPolicy Bypass -File "C:\Users\Administrator\Desktop\新闻\tools\install_boot_task.ps1"
# 结果同时写到本目录的 install_boot_task.log。
#
# 说明:
# - 登录类型 S4U:以当前用户身份、不管是否登录都运行,Windows 不需要保存密码(只访问本机和外网,
#   不访问网络共享,正好够用)。
# - 看门狗有单实例锁(端口 8912):登录后 Startup 快捷方式再启动一份会自动退出,不会重复运行。
# - 没人登录时 v2rayN(VPN) 不会启动:网站、X、DeepSeek 照常;Telegram 推送要等登录、VPN 起来后才恢复。
# - goldhot(黄金板块)不在这个任务里,仍是登录后才启动。
# - 撤销:Unregister-ScheduledTask -TaskName AIHOT-NewsWatchdog-Boot -Confirm:$false

$ErrorActionPreference = 'Stop'
$log = Join-Path $PSScriptRoot 'install_boot_task.log'
function Say($msg) { $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg; Write-Host $line; Add-Content -Path $log -Value $line -Encoding UTF8 }

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Say "失败:需要以管理员身份运行。"
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
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest

    Register-ScheduledTask -TaskName 'AIHOT-NewsWatchdog-Boot' `
        -Description 'AIHOT 新闻站:开机即启动看门狗(不管是否登录)' `
        -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Force | Out-Null

    $t = Get-ScheduledTask -TaskName 'AIHOT-NewsWatchdog-Boot'
    Say ("已注册:{0}  状态:{1}  用户:{2}  登录类型:{3}" -f $t.TaskName, $t.State, $t.Principal.UserId, $t.Principal.LogonType)
} catch {
    Say ("失败:" + $_.Exception.Message)
    exit 1
}
