param(
    [ValidateSet("Apply", "Verify", "Remove")]
    [string]$Action = "Apply",
    [ValidateRange(1, 65535)]
    [int]$Port = 18080,
    [string]$RemoteAddress = "10.0.0.0/255.0.0.0"
)

$ErrorActionPreference = "Stop"
$VmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
$WindowsRuleName = "NJIT-Campus-Dify-$Port"
$HyperVRuleName = "NJIT-Campus-Dify-$Port-HyperV"
$BackupRoot = "C:\ProgramData\NJITCampus\firewall-backups"
$ActiveBackupPath = "$BackupRoot\$Port-active.json"
$LegacyPort80RuleDisplayNames = @("Dify HTTP 80", "dify-nginx-80")

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required."
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
        $backupState = @{
            PreviousWindowsRule = Get-WindowsRuleSnapshot
            PreviousHyperVRule = Get-HyperVRuleSnapshot
            LegacyWindowsRules = $legacyRuleStates
        }
        $backupState | ConvertTo-Json -Depth 6 |
            Set-Content -Encoding UTF8 -Path "$BackupRoot\$Port-$stamp.json"
        if (-not (Test-Path -Path $ActiveBackupPath)) {
            $backupState | ConvertTo-Json -Depth 6 |
                Set-Content -Encoding UTF8 -Path $ActiveBackupPath
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

        Test-CampusFirewallRules
        Write-Output "Campus Dify intranet firewall rules applied for TCP $Port."
    }
    "Verify" {
        Test-CampusFirewallRules
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
            Remove-Item -Path $ActiveBackupPath
        }
        Write-Output "Campus Dify intranet firewall rules removed for TCP $Port."
    }
}
