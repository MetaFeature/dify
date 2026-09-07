param(
    [ValidateSet("Apply", "Verify", "Remove")]
    [string]$Action = "Apply",
    [ValidateRange(1, 65535)]
    [int]$Port = 18080,
    [ValidateRange(1, 65535)]
    [int]$CanaryPort = 18080,
    [ValidateRange(1, 65535)]
    [int]$AdminPort = 18081,
    [ValidateRange(1, 65535)]
    [int]$GatewayPort = 13000,
    [string]$RemoteAddress = "10.0.0.0/255.0.0.0",
    [string]$ListenAddress = "10.20.10.193",
    [string]$WslDistributionName = "Ubuntu-2404"
)

$ErrorActionPreference = "Stop"
$VmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
$WindowsRuleName = "NJIT-Campus-Dify-$Port"
$HyperVRuleName = "NJIT-Campus-Dify-$Port-HyperV"
$BackupRoot = "C:\ProgramData\NJITCampus\firewall-backups"
$ActiveBackupPath = "$BackupRoot\$Port-active.json"
$LegacyPort80RuleDisplayNames = @("Dify HTTP 80", "dify-nginx-80")
$WslConfigPath = Join-Path $env:USERPROFILE ".wslconfig"
$WslKeepaliveTaskName = "wsl-docker-boot"
$CampusControlRoot = "C:\ProgramData\NJITCampus"
$CampusControlScript = Join-Path $CampusControlRoot "campus-control.ps1"
$CampusControlShortcuts = @{
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

function Assert-IPv4Address {
    param(
        [string]$Address,
        [string]$Name
    )

    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed) -or
        $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        throw "$Name must be an IPv4 address."
    }
}

function Get-WslConfigSnapshot {
    if (-not (Test-Path -LiteralPath $WslConfigPath)) {
        return [PSCustomObject]@{ Exists = $false }
    }
    $content = [System.IO.File]::ReadAllBytes($WslConfigPath)
    return [PSCustomObject]@{
        Exists = $true
        ContentBase64 = [Convert]::ToBase64String($content)
    }
}

function Set-WslHostAddressLoopback {
    $lines = if (Test-Path -LiteralPath $WslConfigPath) {
        [System.IO.File]::ReadAllLines($WslConfigPath)
    }
    else {
        @()
    }
    $output = [System.Collections.Generic.List[string]]::new()
    $inExperimental = $false
    $experimentalSeen = $false
    $settingWritten = $false

    foreach ($line in $lines) {
        if ($line -match '^\s*\[([^]]+)\]\s*$') {
            if ($inExperimental -and -not $settingWritten) {
                $output.Add("hostAddressLoopback=true")
                $settingWritten = $true
            }
            $inExperimental = $Matches[1].Trim() -ieq "experimental"
            if ($inExperimental) {
                $experimentalSeen = $true
            }
            $output.Add($line)
            continue
        }
        if ($line -match '^\s*hostAddressLoopback\s*=') {
            if ($inExperimental -and -not $settingWritten) {
                $output.Add("hostAddressLoopback=true")
                $settingWritten = $true
            }
            continue
        }
        $output.Add($line)
    }

    if ($inExperimental -and -not $settingWritten) {
        $output.Add("hostAddressLoopback=true")
        $settingWritten = $true
    }
    if (-not $experimentalSeen) {
        if ($output.Count -gt 0 -and $output[$output.Count - 1] -ne "") {
            $output.Add("")
        }
        $output.Add("[experimental]")
        $output.Add("hostAddressLoopback=true")
    }

    $temporaryPath = "$WslConfigPath.njit-campus-$([Guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllLines(
            $temporaryPath,
            $output,
            [System.Text.UTF8Encoding]::new($false)
        )
        Move-Item -LiteralPath $temporaryPath -Destination $WslConfigPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Test-WslHostAddressLoopbackConfiguration {
    if (-not (Test-Path -LiteralPath $WslConfigPath)) {
        throw "The Windows WSL configuration is missing."
    }
    $section = ""
    $settings = @()
    foreach ($line in [System.IO.File]::ReadAllLines($WslConfigPath)) {
        if ($line -match '^\s*\[([^]]+)\]\s*$') {
            $section = $Matches[1].Trim()
            continue
        }
        if ($line -match '^\s*hostAddressLoopback\s*=\s*([^#;\s]+)') {
            $settings += [PSCustomObject]@{
                Section = $section
                Value = $Matches[1]
            }
        }
    }
    if ($settings.Count -ne 1 -or $settings[0].Section -ine "experimental" -or
        $settings[0].Value -ine "true") {
        throw "WSL mirrored host-address loopback is not enabled exactly once."
    }
}

function Test-CampusHostAddressLoopback {
    $curlPath = Join-Path $env:SystemRoot "System32\curl.exe"
    if (-not (Test-Path -LiteralPath $curlPath)) {
        throw "Windows curl.exe is required for Campus public-entry verification."
    }
    $checks = @(
        [PSCustomObject]@{
            Name = "Campus public root"
            Url = "http://${ListenAddress}:${Port}/"
            Status = "302"
        },
        [PSCustomObject]@{
            Name = "Campus public portal"
            Url = "http://${ListenAddress}:${Port}/portal/"
            Status = "200"
        },
        [PSCustomObject]@{
            Name = "Campus administration portal"
            Url = "http://127.0.0.1:${AdminPort}/"
            Status = "200"
        },
        [PSCustomObject]@{
            Name = "Campus model gateway"
            Url = "http://127.0.0.1:${GatewayPort}/api/status"
            Status = "200"
        },
        [PSCustomObject]@{
            Name = "Campus loopback canary"
            Url = "http://127.0.0.1:${CanaryPort}/health"
            Status = "200"
        }
    )
    foreach ($check in $checks) {
        $verified = $false
        foreach ($attempt in 1..10) {
            $output = @(
                & $curlPath --noproxy "*" --connect-timeout 1 --max-time 2 `
                    --silent --output NUL --write-out "%{http_code}" $check.Url 2>$null
            )
            $status = ($output -join "").Trim()
            if ($LASTEXITCODE -eq 0 -and $status -eq $check.Status) {
                $verified = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $verified) {
            throw "Windows cannot reach $($check.Name) at $($check.Url)."
        }
    }
}

function Test-WslKeepaliveTask {
    param([switch]$RequireRunning)

    $task = Get-ScheduledTask `
        -TaskName $WslKeepaliveTaskName `
        -TaskPath "\" `
        -ErrorAction Stop
    $actions = @($task.Actions)
    $expectedArguments = "-d $WslDistributionName -u root -e /usr/bin/tail -f /dev/null"
    $actualArguments = if ($actions.Count -eq 1) {
        ($actions[0].Arguments -replace '\s+', ' ').Trim()
    }
    else {
        ""
    }
    if ($actions.Count -ne 1 -or
        [System.IO.Path]::GetFileName($actions[0].Execute) -ine "wsl.exe" -or
        $actualArguments -ne $expectedArguments) {
        throw "The Windows WSL keepalive task does not match the Campus runtime anchor."
    }
    $bootTriggers = @(
        $task.Triggers | Where-Object {
            $_.CimClass.CimClassName -eq "MSFT_TaskBootTrigger" -and $_.Enabled
        }
    )
    if ($bootTriggers.Count -lt 1 -or $task.Principal.RunLevel -ne "Highest") {
        throw "The Windows WSL keepalive task is not configured for elevated startup at boot."
    }
    if ($RequireRunning -and $task.State -ne "Running") {
        throw "The Windows WSL keepalive task is not running."
    }
}

function Test-WslCampusHeartbeat {
    $enabled = @(
        & wsl.exe -d $WslDistributionName -u root -e `
            systemctl is-enabled njit-campus-heartbeat.timer 2>$null
    ) -join ""
    if ($LASTEXITCODE -ne 0 -or $enabled.Trim() -ne "enabled") {
        throw "The Campus component heartbeat timer is not enabled in WSL."
    }
    $active = @(
        & wsl.exe -d $WslDistributionName -u root -e `
            systemctl is-active njit-campus-heartbeat.timer 2>$null
    ) -join ""
    if ($LASTEXITCODE -ne 0 -or $active.Trim() -ne "active") {
        throw "The Campus component heartbeat timer is not active in WSL."
    }
}

function Test-CampusControlShortcuts {
    if (-not (Test-Path -LiteralPath $CampusControlScript)) {
        throw "The installed Campus control script is missing."
    }
    $publicDesktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in $CampusControlShortcuts.GetEnumerator()) {
        $shortcutPath = Join-Path $publicDesktop $entry.Value
        if (-not (Test-Path -LiteralPath $shortcutPath)) {
            throw "Campus control shortcut is missing: $($entry.Value)"
        }
        $shortcut = $shell.CreateShortcut($shortcutPath)
        if ([System.IO.Path]::GetFileName($shortcut.TargetPath) -ine "powershell.exe" -or
            $shortcut.Arguments -notlike "*$CampusControlScript*" -or
            $shortcut.Arguments -notlike "*-Action $($entry.Key)*") {
            throw "Campus control shortcut target is invalid: $($entry.Value)"
        }
    }
}

function Get-WindowsRuleSnapshot {
    $rule = Get-NetFirewallRule -Name $WindowsRuleName -ErrorAction SilentlyContinue
    if ($null -eq $rule) {
        return [PSCustomObject]@{ Exists = $false }
    }
    $portFilter = $rule | Get-NetFirewallPortFilter
    $addressFilter = $rule | Get-NetFirewallAddressFilter
    return [PSCustomObject]@{
        Exists = $true
        DisplayName = $rule.DisplayName
        Enabled = [string]$rule.Enabled
        Direction = [string]$rule.Direction
        Action = [string]$rule.Action
        Profile = [string]$rule.Profile
        Protocol = [string]$portFilter.Protocol
        LocalPort = [string]$portFilter.LocalPort
        RemoteAddress = @($addressFilter.RemoteAddress)
    }
}

function Get-HyperVRuleSnapshot {
    $rule = Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue
    if ($null -eq $rule) {
        return [PSCustomObject]@{ Exists = $false }
    }
    return [PSCustomObject]@{
        Exists = $true
        DisplayName = $rule.DisplayName
        Enabled = [string]$rule.Enabled
        Direction = [string]$rule.Direction
        Action = [string]$rule.Action
        VMCreatorId = [string]$rule.VMCreatorId
        Protocol = [string]$rule.Protocol
        LocalPorts = @($rule.LocalPorts)
        RemoteAddresses = @($rule.RemoteAddresses)
    }
}

function Restore-PreviousCampusRules {
    param([object]$Backup)

    if ($Backup.PreviousWindowsRule.Exists -eq $true) {
        New-NetFirewallRule `
            -Name $WindowsRuleName `
            -DisplayName $Backup.PreviousWindowsRule.DisplayName `
            -Direction $Backup.PreviousWindowsRule.Direction `
            -Action $Backup.PreviousWindowsRule.Action `
            -Enabled $Backup.PreviousWindowsRule.Enabled `
            -Profile $Backup.PreviousWindowsRule.Profile `
            -Protocol $Backup.PreviousWindowsRule.Protocol `
            -LocalPort $Backup.PreviousWindowsRule.LocalPort `
            -RemoteAddress @($Backup.PreviousWindowsRule.RemoteAddress) | Out-Null
    }
    if ($Backup.PreviousHyperVRule.Exists -eq $true) {
        New-NetFirewallHyperVRule `
            -Name $HyperVRuleName `
            -DisplayName $Backup.PreviousHyperVRule.DisplayName `
            -Direction $Backup.PreviousHyperVRule.Direction `
            -Action $Backup.PreviousHyperVRule.Action `
            -Enabled $Backup.PreviousHyperVRule.Enabled `
            -VMCreatorId $Backup.PreviousHyperVRule.VMCreatorId `
            -Protocol $Backup.PreviousHyperVRule.Protocol `
            -LocalPorts @($Backup.PreviousHyperVRule.LocalPorts) `
            -RemoteAddresses @($Backup.PreviousHyperVRule.RemoteAddresses) | Out-Null
    }
}

function Restore-PreviousWslConfig {
    param([object]$Backup)

    $property = $Backup.PSObject.Properties["PreviousWslConfig"]
    if ($null -eq $property) {
        return
    }
    if ($Backup.PreviousWslConfig.Exists -ne $true) {
        if (Test-Path -LiteralPath $WslConfigPath) {
            Remove-Item -LiteralPath $WslConfigPath -Force
        }
        return
    }

    $content = [Convert]::FromBase64String($Backup.PreviousWslConfig.ContentBase64)
    $temporaryPath = "$WslConfigPath.njit-campus-$([Guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllBytes($temporaryPath, $content)
        Move-Item -LiteralPath $temporaryPath -Destination $WslConfigPath -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function Test-CampusFirewallRules {
    $windowsRule = Get-NetFirewallRule -Name $WindowsRuleName -ErrorAction Stop
    $windowsPort = $windowsRule | Get-NetFirewallPortFilter
    $windowsAddress = $windowsRule | Get-NetFirewallAddressFilter
    if ($windowsRule.Enabled -ne "True" -or $windowsRule.Direction -ne "Inbound" -or
        $windowsRule.Action -ne "Allow" -or $windowsPort.Protocol -ne "TCP" -or
        [string]$windowsPort.LocalPort -ne [string]$Port -or
        $windowsAddress.RemoteAddress -notcontains $RemoteAddress) {
        throw "Windows Firewall rule does not match the Campus intranet boundary."
    }

    $hyperVRule = Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction Stop
    if ($hyperVRule.Enabled -ne "True" -or $hyperVRule.Direction -ne "Inbound" -or
        $hyperVRule.Action -ne "Allow" -or $hyperVRule.Protocol -ne "TCP" -or
        [string]$hyperVRule.LocalPorts -ne [string]$Port -or
        $hyperVRule.VMCreatorId -ne $VmCreatorId -or
        $hyperVRule.RemoteAddresses -notcontains $RemoteAddress) {
        throw "Hyper-V Firewall rule does not match the Campus WSL boundary."
    }

    if ($Port -eq 80) {
        foreach ($displayName in $LegacyPort80RuleDisplayNames) {
            $enabledLegacyRules = @(
                Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue |
                    Where-Object Enabled -eq "True"
            )
            if ($enabledLegacyRules.Count -gt 0) {
                throw "Legacy port-80 firewall rule remains enabled: $displayName"
            }
        }
    }
}

Assert-Administrator
Assert-IPv4Address -Address $ListenAddress -Name "ListenAddress"

switch ($Action) {
    "Apply" {
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $legacyRuleStates = @()
        if ($Port -eq 80) {
            foreach ($displayName in $LegacyPort80RuleDisplayNames) {
                foreach ($rule in @(Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue)) {
                    $legacyRuleStates += [PSCustomObject]@{
                        Name = $rule.Name
                        DisplayName = $rule.DisplayName
                        Enabled = [string]$rule.Enabled
                    }
                }
            }
        }
        $previousWslConfig = Get-WslConfigSnapshot
        $backupState = @{
            PreviousWindowsRule = Get-WindowsRuleSnapshot
            PreviousHyperVRule = Get-HyperVRuleSnapshot
            PreviousWslConfig = $previousWslConfig
            LegacyWindowsRules = $legacyRuleStates
        }
        $backupState | ConvertTo-Json -Depth 6 |
            Set-Content -Encoding UTF8 -Path "$BackupRoot\$Port-$stamp.json"
        if (-not (Test-Path -Path $ActiveBackupPath)) {
            $backupState | ConvertTo-Json -Depth 6 |
                Set-Content -Encoding UTF8 -Path $ActiveBackupPath
        }
        else {
            $activeBackup = Get-Content -Raw -Path $ActiveBackupPath | ConvertFrom-Json
            if ($null -eq $activeBackup.PSObject.Properties["PreviousWslConfig"]) {
                $activeBackup | Add-Member `
                    -NotePropertyName PreviousWslConfig `
                    -NotePropertyValue $previousWslConfig
                $activeBackup | ConvertTo-Json -Depth 6 |
                    Set-Content -Encoding UTF8 -Path $ActiveBackupPath
            }
        }

        foreach ($legacyRule in $legacyRuleStates) {
            Get-NetFirewallRule -Name $legacyRule.Name -ErrorAction Stop |
                Set-NetFirewallRule -Enabled False
        }

        Get-NetFirewallRule -Name $WindowsRuleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule
        New-NetFirewallRule `
            -Name $WindowsRuleName `
            -DisplayName "NJIT Campus Dify TCP $Port" `
            -Direction Inbound `
            -Action Allow `
            -Enabled True `
            -Profile Any `
            -Protocol TCP `
            -LocalPort $Port `
            -RemoteAddress $RemoteAddress | Out-Null

        Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallHyperVRule
        New-NetFirewallHyperVRule `
            -Name $HyperVRuleName `
            -DisplayName "NJIT Campus Dify WSL TCP $Port" `
            -Direction Inbound `
            -Action Allow `
            -Enabled True `
            -VMCreatorId $VmCreatorId `
            -Protocol TCP `
            -LocalPorts $Port `
            -RemoteAddresses $RemoteAddress | Out-Null

        Set-WslHostAddressLoopback
        Test-CampusFirewallRules
        Test-WslHostAddressLoopbackConfiguration
        Test-WslKeepaliveTask
        Write-Output "Campus Dify intranet boundary applied for TCP $Port; restart WSL before Verify."
    }
    "Verify" {
        Test-CampusFirewallRules
        Test-WslHostAddressLoopbackConfiguration
        Test-WslKeepaliveTask -RequireRunning
        Test-WslCampusHeartbeat
        Test-CampusControlShortcuts
        Test-CampusHostAddressLoopback
        Write-Output "Campus Dify intranet firewall rules verified for TCP $Port."
    }
    "Remove" {
        Get-NetFirewallRule -Name $WindowsRuleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallRule
        Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue |
            Remove-NetFirewallHyperVRule
        if (Test-Path -Path $ActiveBackupPath) {
            $backup = Get-Content -Raw -Path $ActiveBackupPath | ConvertFrom-Json
            foreach ($legacyRule in @($backup.LegacyWindowsRules)) {
                $enabled = if ($legacyRule.Enabled -eq "True") { "True" } else { "False" }
                Get-NetFirewallRule -Name $legacyRule.Name -ErrorAction SilentlyContinue |
                    Set-NetFirewallRule -Enabled $enabled
            }
            Restore-PreviousCampusRules -Backup $backup
            Restore-PreviousWslConfig -Backup $backup
            Remove-Item -Path $ActiveBackupPath
        }
        Write-Output "Campus Dify intranet boundary removed for TCP $Port; restart WSL to finish rollback."
    }
}
