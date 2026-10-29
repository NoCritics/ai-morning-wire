# Opens the AI Morning Wire's Claude Code session each morning. Task Scheduler runs this at the configured time,
# 95 minutes later and at logon. runstate.py next-action decides: launch or retry opens a fresh session in a new
# Windows Terminal window, mark-failed closes a day whose run never finished, nothing stands down.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\morning.ps1 -Python <python.exe> -Claude <claude.exe> [-DryRun] [-IgnoreTime]
# -IgnoreTime (setup's first run) passes --ignore-time to next-action, which treats the scheduled time as 00:00.
# -DryRun still runs next-action for real (it is the decision, and may close an earlier open day or log a config
# error); the started, retry and mark-failed calls and the wt.exe command line are only printed.
param(
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$Claude,
    [switch]$DryRun,
    [switch]$IgnoreTime
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunStateScript = Join-Path $ProjectRoot 'runstate.py'
$BuildScript = Join-Path $ProjectRoot 'build.py'

# Same line format as store.append_run_log, kept here so the script can log when Python itself fails. -DryRun prints instead.
function Write-RunLog($Message) {
    if ($DryRun) {
        Write-Output "dry run: $Message"
        return
    }
    $DataFolder = Join-Path $ProjectRoot 'data'
    New-Item -ItemType Directory -Force -Path $DataFolder | Out-Null
    [System.IO.File]::AppendAllText((Join-Path $DataFolder 'runs.log'), (Get-Date).ToString('yyyy-MM-dd HH:mm', [System.Globalization.CultureInfo]::InvariantCulture) + ' ' + $Message + "`n")
}

# Shows a command line the way it would be typed, with quotes around arguments that contain spaces.
function Format-CommandLine($Executable, $Arguments) {
    $Quoted = @()
    foreach ($Part in (@($Executable) + $Arguments)) {
        if ($Part -match '\s') {
            $Quoted += '"' + $Part + '"'
        } else {
            $Quoted += $Part
        }
    }
    return $Quoted -join ' '
}

# Runs a Python script from the project and returns its exit code, or $null when Python could not be started.
function Invoke-Python($ScriptPath, $ScriptArguments) {
    if ($DryRun) {
        Write-Host ('dry run: would run ' + (Format-CommandLine $Python (@($ScriptPath) + $ScriptArguments)))
        return 0
    }
    $script:LASTEXITCODE = 0
    try {
        & $Python $ScriptPath @ScriptArguments | Out-Host
    } catch {
        return $null
    }
    return $LASTEXITCODE
}

function Stop-AfterPythonFailure($Message) {
    Write-RunLog "morning task: $Message"
    exit 1
}

# Runs runstate.py with one command and stops the script when Python cannot start or the command fails.
function Invoke-RunStateOrStop($Command) {
    $ExitCode = Invoke-Python $RunStateScript @($Command)
    if ($null -eq $ExitCode) {
        Stop-AfterPythonFailure "could not run Python for $Command"
    }
    if ($ExitCode -ne 0) {
        Stop-AfterPythonFailure "runstate.py $Command exited with $ExitCode"
    }
}

# next-action is the decision, so it runs for real even in a dry run.
$NextActionArguments = @($RunStateScript, 'next-action')
if ($IgnoreTime) {
    $NextActionArguments += '--ignore-time'
}
$ActionLines = $null
$NextActionExit = $null
try {
    $LASTEXITCODE = 0
    $ActionLines = & $Python @NextActionArguments
    $NextActionExit = $LASTEXITCODE
} catch {
    Stop-AfterPythonFailure "could not run Python ($($_.Exception.Message))"
}
if ($NextActionExit -eq 3) {
    # config.toml is broken: runstate.py already logged it and rebuilt the page, so there is nothing to do.
    Write-RunLog 'morning task: config.toml is broken, nothing launched'
    exit 0
}
if ($NextActionExit -ne 0) {
    Stop-AfterPythonFailure "runstate.py next-action exited with $NextActionExit"
}
$DecisionLine = [string](@($ActionLines)[0])
$SeparatorIndex = $DecisionLine.IndexOf(':')
if ($SeparatorIndex -lt 1) {
    Stop-AfterPythonFailure "runstate.py next-action printed '$DecisionLine'"
}
$Action = $DecisionLine.Substring(0, $SeparatorIndex).Trim()
$Reason = $DecisionLine.Substring($SeparatorIndex + 1).Trim()

if ($Action -eq 'nothing') {
    Write-RunLog "morning task: $Reason"
    exit 0
}

if ($Action -eq 'mark-failed') {
    Invoke-RunStateOrStop 'mark-failed'
    Write-RunLog "morning task: marked the day failed ($Reason)"
    exit 0
}

# No ; or " in the prompts: Windows Terminal reads ; as a command separator.
# acceptEdits lets the unattended session write the run's files inside the repo folder without asking; the commands it
# runs are covered by the allow rules in .claude\settings.json and .claude\settings.local.json (written by setup.ps1).
if ($Action -eq 'launch') {
    $StartCommand = 'started'
    $Prompt = 'Morning run: follow docs/daily-run.md exactly.'
} elseif ($Action -eq 'retry') {
    $StartCommand = 'retry'
    $Prompt = 'Morning run (retry): follow docs/daily-run.md exactly.'
} else {
    Stop-AfterPythonFailure "runstate.py next-action printed the unknown action '$Action'"
}

Invoke-RunStateOrStop $StartCommand

$TerminalExe = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
$LaunchArguments = @(
    '-w', 'new', '-d', $ProjectRoot,
    $Claude, '--model', 'sonnet', '--permission-mode', 'acceptEdits', $Prompt
)
$LaunchFailure = $null
if ($DryRun) {
    Write-Output ('dry run: would run ' + (Format-CommandLine $TerminalExe $LaunchArguments))
} else {
    $LASTEXITCODE = 0
    try {
        & $TerminalExe @LaunchArguments
        if ($LASTEXITCODE -ne 0) {
            $LaunchFailure = "wt.exe exited with $LASTEXITCODE"
        }
    } catch {
        $LaunchFailure = $_.Exception.Message
    }
}
if ($null -ne $LaunchFailure) {
    # started or retry is already in the log, so close the day instead of leaving it to the watchdog.
    $FailedToday = (Get-Date).ToString('yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
    [void](Invoke-Python $RunStateScript @('failed', $FailedToday, "the morning window did not open: $LaunchFailure"))
    [void](Invoke-Python $BuildScript @())
    Write-RunLog "morning task: launch failed: $LaunchFailure"
    exit 1
}
Write-RunLog "morning task: opened the $Action session in a new terminal"
