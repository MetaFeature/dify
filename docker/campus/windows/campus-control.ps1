param(
    [ValidateSet("Start", "Stop", "Restart", "Status", "InstallShortcuts")]
    [string]$Action = "Status",
    [string]$WslDistributionName = "Ubuntu-2404",
    [string]$CampusDockerPath = "/opt/njit-campus-phase1/dify/docker"
)

$ErrorActionPreference = "Stop"
$KeepaliveTaskName = "wsl-docker-boot"
$InstallRoot = "C:\ProgramData\NJITCampus"
$InstalledScript = Join-Path $InstallRoot "campus-control.ps1"
$ShortcutNames = @{
    Start = "NJIT Campus - Start.lnk"
    Stop = "NJIT Campus - Stop.lnk"
    Restart = "NJIT Campus - Restart.lnk"
    Status = "NJIT Campus - Status.lnk"
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required."
    }
}

function Invoke-WslManager {
    param([string]$ManagerAction)

    $command = "cd '$CampusDockerPath' && ./campus/manage.sh $ManagerAction"
    & wsl.exe -d $WslDistributionName -- bash -lc $command
    if ($LASTEXITCODE -ne 0) {
        throw "Campus manager action failed: $ManagerAction"
    }
}

function Start-CampusRuntime {
    $task = Get-ScheduledTask -TaskName $KeepaliveTaskName -TaskPath "\" -ErrorAction Stop
    if ($task.State -ne "Running") {
        Start-ScheduledTask -TaskName $KeepaliveTaskName -TaskPath "\"
    }
    & wsl.exe -d $WslDistributionName -u root -e systemctl start docker.service
    if ($LASTEXITCODE -ne 0) {
        throw "Docker did not start inside WSL."
    }
}

function Install-CampusShortcuts {
    Assert-Administrator
    $publicDesktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $backupRoot = Join-Path $InstallRoot "control-backups"
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $backupPath = Join-Path $backupRoot $stamp
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

    $targets = @($InstalledScript)
    foreach ($shortcutName in $ShortcutNames.Values) {
        $targets += Join-Path $publicDesktop $shortcutName
    }
    foreach ($target in $targets) {
        if (Test-Path -LiteralPath $target) {
            Copy-Item -LiteralPath $target -Destination $backupPath -Force
        }
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    Copy-Item -LiteralPath $PSCommandPath -Destination $InstalledScript -Force
    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in $ShortcutNames.GetEnumerator()) {
        $shortcutPath = Join-Path $publicDesktop $entry.Value
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $shortcut.Arguments = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$InstalledScript`" -Action $($entry.Key)"
        $shortcut.WorkingDirectory = $InstallRoot
        $shortcut.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
        $shortcut.Save()
    }
    Write-Output "Campus control shortcuts installed; rollback backup: $backupPath"
}

switch ($Action) {
    "Start" {
        Start-CampusRuntime
        Invoke-WslManager -ManagerAction "start"
    }
    "Stop" {
        Invoke-WslManager -ManagerAction "stop"
    }
    "Restart" {
        Start-CampusRuntime
        Invoke-WslManager -ManagerAction "restart"
    }
    "Status" {
        Invoke-WslManager -ManagerAction "status"
    }
    "InstallShortcuts" {
        Install-CampusShortcuts
    }
}
