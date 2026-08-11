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

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required."
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
}

Assert-Administrator

switch ($Action) {
    "Apply" {
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        @{
            WindowsRule = Get-NetFirewallRule -Name $WindowsRuleName -ErrorAction SilentlyContinue
            HyperVRule = Get-NetFirewallHyperVRule -Name $HyperVRuleName -ErrorAction SilentlyContinue
        } | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -Path "$BackupRoot\$stamp.json"

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
        Write-Output "Campus Dify intranet firewall rules removed for TCP $Port."
    }
}
