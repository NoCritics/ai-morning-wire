# Removes the AI Morning Wire's scheduled task. Never deletes files: it prints the folder to delete.
# Usage: powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-TaskName <name>] [-Yes]
# -Yes approves the confirmation.
param(
    [string]$TaskName = 'AI Morning Wire',
    [switch]$Yes
)

$ProjectRoot = $PSScriptRoot

function Write-FolderNote {
    Write-Host ''
    Write-Host 'No files were deleted. To remove the program itself, delete this folder (it also holds your config.toml and data\):'
    Write-Host "  $ProjectRoot"
}

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $ExistingTask) {
    Write-Host "No scheduled task named '$TaskName' was found. Nothing to unregister."
    Write-FolderNote
    exit 0
}

$UnregisterCommand = "Unregister-ScheduledTask -TaskName '" + $TaskName.Replace("'", "''") + "' -Confirm:`$false -ErrorAction Stop"
Write-Host "This removes the scheduled task '$TaskName'. This is the exact command:"
Write-Host ''
Write-Host $UnregisterCommand
Write-Host ''
$Approved = $Yes
if ($Yes) {
    Write-Host 'Unregister this task? -> yes (-Yes)'
} else {
    while ($true) {
        $Raw = Read-Host 'Unregister this task? [y/N]'
        if ($null -eq $Raw) { break }
        $Typed = $Raw.Trim().ToLower()
        if ($Typed -eq 'y' -or $Typed -eq 'yes') { $Approved = $true; break }
        if ($Typed -eq '' -or $Typed -eq 'n' -or $Typed -eq 'no') { break }
        Write-Host 'Please answer y or n.'
    }
}
if (-not $Approved) {
    Write-Host 'Left the task in place.'
    exit 0
}

try {
    & ([scriptblock]::Create($UnregisterCommand))
} catch {
    if ($_.Exception.Message -match 'Access is denied|0x80070005|UnauthorizedAccess|PermissionDenied') {
        Write-Host 'Windows refused to remove the task (access denied). Fix: right-click PowerShell, choose Run as administrator, and run uninstall.ps1 again.' -ForegroundColor Red
        exit 1
    }
    Write-Host "The task could not be removed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
if ($null -ne (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Write-Host "Unregister-ScheduledTask finished but '$TaskName' is still there. Fix: right-click PowerShell, choose Run as administrator, and run uninstall.ps1 again." -ForegroundColor Red
    exit 1
}
Write-Host "Removed '$TaskName'." -ForegroundColor Green
Write-FolderNote
