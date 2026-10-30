# Sets up the AI Morning Wire on this PC: checks the prerequisites, asks a few questions, writes config.toml and
# .claude\settings.local.json, registers ONE scheduled task (after a yes) and runs the required first run.
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1 [-TaskName <name>] [-Time HH:MM] [-Defaults] [-Yes] [-DryRun]
# Every question's default comes from an existing config.toml (else the public defaults: opt-in sources, the claude.ai page and
# the browser fallback off, 08:00). -Defaults answers every question with its default. -TaskName defaults to the config's task_name.
# When config.toml is kept, only its time line is rewritten, and only when the time differs.
# -Yes approves every confirmation (replacing an existing task, registering) except replacing an existing config.toml, which
# only an interactive yes does: a hand-tuned file is never overwritten by a flag. The first run is not optional: it runs
# right after registration, because the folder's allow rules stay off until its trust prompt is accepted once.
# -DryRun prints every check, file and command instead of running it and changes nothing on disk or in Task Scheduler.
param(
    [string]$TaskName = '',
    [string]$Time = '',
    [switch]$Defaults,
    [switch]$Yes,
    [switch]$DryRun
)

$ProjectRoot = $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot 'config.toml'
$SettingsFolder = Join-Path $ProjectRoot '.claude'
$SettingsPath = Join-Path $SettingsFolder 'settings.local.json'
$MorningScript = Join-Path $ProjectRoot 'scripts\morning.ps1'
$TimePattern = '^([01]\d|2[0-3]):[0-5]\d$'
$RetryDelayMinutes = 95
$MaxRootLength = 115
$PromptScripts = @('collect.py', 'browser_source.py', 'summarize.py', 'assemble.py', 'build.py', 'runstate.py')
$PlaywrightServerNames = @()
$DefaultTaskName = 'AI Morning Wire'
$RunbookBrowserTools = @('browser_navigate', 'browser_wait_for', 'browser_evaluate', 'browser_close')
$ArtifactRules = @('Artifact', 'ArtifactData', 'Skill(artifact-capabilities)', 'Skill(artifact-design)')

function Stop-Setup($Message) {
    Write-Host ''
    Write-Host "Setup stopped: $Message" -ForegroundColor Red
    exit 1
}

function Write-Step($Message) {
    Write-Host ''
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Utf8NoBom($Path, $Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# Asks until the answer is y, yes, n or no; a blank answer means the default. At the end of input it returns $AnswerAtEndOfInput.
function Read-YesNo($Question, $DefaultYes, $AnswerAtEndOfInput) {
    $Hint = if ($DefaultYes) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $Raw = Read-Host "$Question $Hint"
        if ($null -eq $Raw) { return $AnswerAtEndOfInput }
        $Typed = $Raw.Trim().ToLower()
        if ($Typed -eq '') { return $DefaultYes }
        if ($Typed -eq 'y' -or $Typed -eq 'yes') { return $true }
        if ($Typed -eq 'n' -or $Typed -eq 'no') { return $false }
        Write-Host 'Please answer y or n.'
    }
}

# A question: -Defaults answers it with its default.
function Ask-YesNo($Question, $DefaultYes) {
    if ($Defaults) {
        $Hint = if ($DefaultYes) { '[Y/n]' } else { '[y/N]' }
        $Answer = if ($DefaultYes) { 'yes' } else { 'no' }
        Write-Host "$Question $Hint -> $Answer (default)"
        return $DefaultYes
    }
    return Read-YesNo $Question $DefaultYes $DefaultYes
}

# A confirmation: -Yes approves it. A dry run changes nothing, so it does not ask.
function Confirm-Step($Question, $DefaultYes) {
    if ($Yes) {
        Write-Host "$Question -> yes (-Yes)"
        return $true
    }
    if ($DryRun) {
        Write-Host "$Question -> dry run, not asked"
        return $true
    }
    return Read-YesNo $Question $DefaultYes $false
}

# Quotes a value as a PowerShell single-quoted string, so a printed command is exactly the one that runs.
function Format-PowerShellString($Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}

# A TOML basic string: backslashes doubled, quotes escaped.
function Format-TomlString($Value) {
    return '"' + $Value.Replace('\', '\\').Replace('"', '\"') + '"'
}

function Format-TomlBool($Value) {
    if ($Value) { return 'true' }
    return 'false'
}

# The rule for one interpreter, the way the runbook types it in the Bash tool (the only shell the folder allows). A configured
# python path is typed in forward slashes and double quotes (Git Bash drops the backslashes of an unquoted Windows path), as
# "C:/dir/python.exe" script args. Without a path the runbook types py -3 script args.
function Get-InterpreterRule($PythonValue, $ScriptName) {
    if ($PythonValue -eq '') {
        return ('Bash(py -3 ' + $ScriptName + ' *)')
    }
    $ForwardPath = $PythonValue.Replace([string][char]92, '/')
    return ('Bash("' + $ForwardPath + '" ' + $ScriptName + ' *)')
}

function Test-AccessDenied($ErrorMessage) {
    return ($ErrorMessage -match 'Access is denied|0x80070005|UnauthorizedAccess|PermissionDenied')
}

# Runs a program and returns its exit code and output lines. Python is told to print UTF-8 and PowerShell to read UTF-8,
# so a path with non-ASCII letters (u-umlaut, Cyrillic) arrives intact instead of through the OEM code page.
function Invoke-Utf8($Executable, $Arguments) {
    $PreviousEncoding = $null
    try {
        $PreviousEncoding = [Console]::OutputEncoding
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    } catch {
        $PreviousEncoding = $null
    }
    $global:LASTEXITCODE = 0
    try {
        $Output = & $Executable @($Arguments) 2>&1
        $ExitCode = $global:LASTEXITCODE
    } finally {
        if ($null -ne $PreviousEncoding) {
            try { [Console]::OutputEncoding = $PreviousEncoding } catch { }
        }
    }
    $Lines = @()
    foreach ($OutputLine in @($Output)) {
        $Lines += [string]$OutputLine
    }
    return @{ Exit = $ExitCode; Lines = $Lines }
}

# The last non-empty line of a Python traceback is the error itself.
function Get-LastLine($Lines) {
    $Last = ''
    foreach ($Line in $Lines) {
        if ($Line.Trim() -ne '') { $Last = $Line.Trim() }
    }
    return $Last
}

# Loads config.toml through ainews.config in Python. Returns the exit code, the settings (when they loaded) and the error line
# (empty when they loaded).
function Read-ConfigWithPython($PythonPath) {
    $LoadCode = 'import json, sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from ainews import config; print(json.dumps(config.load_config(Path(sys.argv[1]))))'
    $Result = Invoke-Utf8 $PythonPath @('-X', 'utf8', '-c', $LoadCode, $ProjectRoot)
    if ($Result.Exit -ne 0) {
        return @{ Exit = $Result.Exit; Config = $null; Error = (Get-LastLine $Result.Lines) }
    }
    try {
        $Config = ConvertFrom-Json -InputObject (Get-LastLine $Result.Lines)
    } catch {
        return @{ Exit = 0; Config = $null; Error = 'its settings could not be read back' }
    }
    return @{ Exit = 0; Config = $Config; Error = '' }
}

# ---- Checks --------------------------------------------------------------------------------------------------------

function Find-Python($ProbeCode) {
    $Problems = @()
    $Candidates = @(
        @{ Label = 'py -3'; Command = 'py'; Arguments = @('-3') },
        @{ Label = 'python'; Command = 'python'; Arguments = @() }
    )
    foreach ($Candidate in $Candidates) {
        $AllFound = @(Get-Command $Candidate.Command -CommandType Application -All -ErrorAction SilentlyContinue)
        if ($AllFound.Count -eq 0) {
            $Problems += "'$($Candidate.Label)' was not found."
            continue
        }
        foreach ($Found in $AllFound) {
            $FoundPath = $Found.Source
            if ($Candidate.Command -eq 'python' -and $FoundPath -like '*\WindowsApps\*') {
                $Problems += "'python' at $FoundPath is the Microsoft Store stub, not a real Python."
                continue
            }
            $Probe = $null
            try {
                $Probe = Invoke-Utf8 $FoundPath (@($Candidate.Arguments) + @('-X', 'utf8', '-c', $ProbeCode))
            } catch {
                $Problems += "'$($Candidate.Label)' at $FoundPath could not be run: $($_.Exception.Message)"
                continue
            }
            $Lines = $Probe.Lines
            if ($Probe.Exit -ne 0 -or $Lines.Count -lt 3) {
                $Problems += "'$($Candidate.Label)' at $FoundPath did not report a Python version."
                continue
            }
            $Executable = $Lines[$Lines.Count - 3].Trim()
            $Major = 0
            $Minor = 0
            if (-not [int]::TryParse($Lines[$Lines.Count - 2].Trim(), [ref]$Major) -or -not [int]::TryParse($Lines[$Lines.Count - 1].Trim(), [ref]$Minor)) {
                $Problems += "'$($Candidate.Label)' at $FoundPath printed an unreadable Python version."
                continue
            }
            if ($Executable -like '*\WindowsApps\*') {
                $Problems += "'$($Candidate.Label)' resolves to $Executable, a Microsoft Store Python."
                continue
            }
            if ($Major -lt 3 -or ($Major -eq 3 -and $Minor -lt 11)) {
                $Problems += "'$($Candidate.Label)' at $FoundPath is Python $Major.$Minor ($Executable); 3.11 or newer is needed."
                continue
            }
            return @{ Path = $Executable; Version = "$Major.$Minor" }
        }
    }
    Write-Host ''
    foreach ($Problem in $Problems) {
        Write-Host "  - $Problem"
    }
    Stop-Setup 'no usable Python 3.11+ was found. Fix: install Python 3.11 or newer from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH" and keep the py launcher), or turn off the Store stub under Settings > Apps > Advanced app settings > App execution aliases, then run setup again.'
}

function Test-ClaudeLogin($ClaudePath) {
    if ($DryRun) {
        Write-Host 'dry run: would run: claude -p "reply ok" --model sonnet   (must answer within 60 seconds)'
        return
    }
    Write-Host 'Checking that Claude Code is logged in (up to 60 seconds)...'
    $Job = Start-Job -ScriptBlock {
        param($Executable)
        $Text = & $Executable -p 'reply ok' --model sonnet 2>&1 | Out-String
        [pscustomobject]@{ Exit = $LASTEXITCODE; Output = $Text }
    } -ArgumentList $ClaudePath
    if ($null -eq (Wait-Job -Job $Job -Timeout 60)) {
        Stop-Job -Job $Job
        Remove-Job -Job $Job -Force
        Stop-Setup 'claude did not answer within 60 seconds. Fix: open a terminal, run `claude` once, log in, then run setup again.'
    }
    $Result = Receive-Job -Job $Job
    Remove-Job -Job $Job -Force
    if ($Result.Exit -ne 0 -or [string]::IsNullOrWhiteSpace($Result.Output)) {
        $Detail = ([string]$Result.Output).Trim()
        Stop-Setup "claude -p did not work (exit $($Result.Exit)): $Detail  Fix: open a terminal, run `claude` once, log in, then run setup again."
    }
    Write-Host 'Claude Code answered.'
}

# Git Bash, where the morning session runs every command (the folder turns off Claude Code's PowerShell tool). Found the way
# Claude Code finds it: CLAUDE_CODE_GIT_BASH_PATH, else bin\bash.exe in the Git folder above a git.exe on PATH. The WSL
# bash in C:\Windows\System32 does not count.
function Find-GitBash {
    if (-not [string]::IsNullOrEmpty($env:CLAUDE_CODE_GIT_BASH_PATH) -and (Test-Path -LiteralPath $env:CLAUDE_CODE_GIT_BASH_PATH)) {
        return $env:CLAUDE_CODE_GIT_BASH_PATH
    }
    $Candidates = @()
    foreach ($GitCommand in @(Get-Command git -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $Folder = Split-Path -Parent $GitCommand.Source
        for ($Level = 0; $Level -lt 3; $Level++) {
            $Folder = Split-Path -Parent $Folder
            if ([string]::IsNullOrEmpty($Folder)) {
                break
            }
            $Candidates += (Join-Path $Folder 'bin\bash.exe')
        }
    }
    foreach ($Candidate in $Candidates) {
        if ($Candidate -notlike "$env:SystemRoot\*" -and (Test-Path -LiteralPath $Candidate)) {
            return $Candidate
        }
    }
    return $null
}

function Test-WindowsTerminal {
    $StorePath = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
    return (Test-Path -LiteralPath $StorePath)
}

# ---- Opt-in warnings, worded from the README's "Sources and their terms" --------------------------------------------

function Write-AlphaSignalWarning {
    Write-Host ''
    Write-Host 'AlphaSignal: its terms forbid automated reading of the site without permission.'
    Write-Host 'Leave it off unless you accept that risk (README: Sources and their terms).'
}

function Write-TelegramWarning {
    Write-Host ''
    Write-Host 'Telegram: its terms forbid scraping channels, including for AI use.'
    Write-Host 'Leave it off unless you accept that risk (README: Sources and their terms).'
}

function Write-BrowserFallbackWarning {
    Write-Host ''
    Write-Host 'Browser fallback: pages that block the collector open in a real browser, which gets past bot protection.'
    Write-Host 'Respect each site''s terms (README: Sources and their terms).'
}

# A permission rule names an MCP server with every character other than letters, digits, _ and - turned into _
# (the plugin server "plugin:playwright:playwright" becomes mcp__plugin_playwright_playwright__*).
function Get-McpRuleServerName($ListedName) {
    return [regex]::Replace($ListedName, '[^A-Za-z0-9_-]', '_')
}

# Each line of `claude mcp list` is "<server name>: <command or url> - <status>"; returns the names that contain "playwright".
function Get-PlaywrightServerNames($Listing) {
    $Names = @()
    foreach ($ListingLine in ($Listing -split "`r?`n")) {
        $SeparatorIndex = $ListingLine.IndexOf(': ')
        if ($SeparatorIndex -lt 1) {
            continue
        }
        $ListedName = $ListingLine.Substring(0, $SeparatorIndex).Trim()
        if ($ListedName -match '(?i)playwright' -and $Names -notcontains $ListedName) {
            $Names += $ListedName
        }
    }
    return $Names
}

function Test-PlaywrightServer($ClaudePath) {
    if ($DryRun) {
        Write-Host 'dry run: would run: claude mcp list   (its output must contain "playwright")'
        Write-Host 'dry run: assuming the server is named playwright, so the allow rule below uses mcp__playwright__*'
        $script:PlaywrightServerNames = @('playwright')
        return $true
    }
    $Listing = ''
    try {
        $Listing = & $ClaudePath mcp list 2>&1 | Out-String
    } catch {
        $Listing = ''
    }
    $FoundNames = @(Get-PlaywrightServerNames $Listing)
    if ($FoundNames.Count -gt 0) {
        $script:PlaywrightServerNames = $FoundNames
        Write-Host ('The Playwright MCP server is configured: ' + ($FoundNames -join ', '))
        return $true
    }
    Write-Host 'The Playwright MCP server is not configured in Claude Code, so the browser fallback stays off.'
    Write-Host 'To add it, run: claude mcp add playwright -- npx @playwright/mcp@latest'
    Write-Host 'then run setup.ps1 again and answer yes to the browser fallback (setup writes the allow rules for it; editing'
    Write-Host 'browser_fallback by hand would leave the morning run waiting on permission prompts).'
    return $false
}

# Rewrites only the top-level time line of a kept config.toml (adds one at the top when it has none), keeping everything else.
function Set-ConfigTimeLine($Path, $NewTime) {
    $Text = [System.IO.File]::ReadAllText($Path)
    $NewLine = 'time = ' + (Format-TomlString $NewTime)
    $TimeLinePattern = New-Object System.Text.RegularExpressions.Regex('(?m)^[ \t]*time[ \t]*=[^\r\n]*')
    if ($TimeLinePattern.IsMatch($Text)) {
        $NewText = $TimeLinePattern.Replace($Text, $NewLine, 1)
    } else {
        $LineBreak = "`n"
        if ($Text.Contains("`r`n")) { $LineBreak = "`r`n" }
        $NewText = $NewLine + $LineBreak + $Text
    }
    Write-Utf8NoBom $Path $NewText
}

# ---- Settings file ---------------------------------------------------------------------------------------------------

# Merges the allow rules into an existing .claude\settings.local.json: adds the wanted rules, keeps other keys and other rules, and
# drops the existing rules that match $RemovePatterns (the script rules, and the rules of a feature that is now off), so running
# setup again after turning a feature off also takes its permissions away.
function Get-MergedSettingsJson($Rules, $RemovePatterns) {
    $Settings = New-Object PSObject
    if (Test-Path -LiteralPath $SettingsPath) {
        $ExistingText = [System.IO.File]::ReadAllText($SettingsPath)
        if (-not [string]::IsNullOrWhiteSpace($ExistingText)) {
            try {
                $Settings = ConvertFrom-Json -InputObject $ExistingText
            } catch {
                Stop-Setup "$SettingsPath is not valid JSON ($($_.Exception.Message)). Fix: correct or delete the file, then run setup again; it was left unchanged."
            }
            if ($Settings -isnot [System.Management.Automation.PSCustomObject]) {
                Stop-Setup "$SettingsPath is not a JSON object. Fix: correct or delete the file, then run setup again; it was left unchanged."
            }
        }
    }
    if (-not ($Settings.PSObject.Properties.Name -contains 'permissions')) {
        Add-Member -InputObject $Settings -NotePropertyName 'permissions' -NotePropertyValue (New-Object PSObject)
    }
    $Permissions = $Settings.permissions
    if ($Permissions -isnot [System.Management.Automation.PSCustomObject]) {
        Stop-Setup "'permissions' in $SettingsPath is not an object. Fix: correct the file, then run setup again; it was left unchanged."
    }
    if (-not ($Permissions.PSObject.Properties.Name -contains 'allow')) {
        Add-Member -InputObject $Permissions -NotePropertyName 'allow' -NotePropertyValue @()
    }
    $Allow = @()
    foreach ($ExistingRule in @($Permissions.allow)) {
        $Keep = ($null -ne $ExistingRule -and $Rules -notcontains $ExistingRule)
        foreach ($RemovePattern in $RemovePatterns) {
            if ($Keep -and ([string]$ExistingRule) -match $RemovePattern) {
                $Keep = $false
            }
        }
        if ($Keep) {
            $Allow += $ExistingRule
        }
    }
    foreach ($Rule in $Rules) {
        $Allow += $Rule
    }
    $Permissions.allow = $Allow
    $Json = ConvertTo-Json -InputObject $Settings -Depth 20
    # Windows PowerShell 5.1 writes < > & ' as \u escapes; they are valid JSON, so they are left as they are.
    return $Json
}

# ---- Scheduled task ----------------------------------------------------------------------------------------------------

function Get-RetryTime($RunTime) {
    $RetryMoment = [datetime]::ParseExact($RunTime, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture).AddMinutes($RetryDelayMinutes)
    return $RetryMoment.ToString('HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
}

# Joins powershell.exe arguments into the line that is printed and registered: anything that is not a plain word (a path) is
# double-quoted.
function Format-ArgumentLine($Arguments) {
    $Words = @()
    foreach ($Argument in $Arguments) {
        if ($Argument -match '^[-\w]+$') {
            $Words += $Argument
        } else {
            $Words += '"' + $Argument + '"'
        }
    }
    return ($Words -join ' ')
}

function Get-RegisterCommand($MorningArguments, $RunTime) {
    $RetryTime = Get-RetryTime $RunTime
    $UserName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $LastIndex = $MorningArguments.Count - 1
    $TaskArguments = $MorningArguments[0..2] + @('-WindowStyle', 'Hidden') + $MorningArguments[3..$LastIndex]
    $ActionArguments = Format-ArgumentLine $TaskArguments
    $Lines = @()
    $Lines += '$Action = New-ScheduledTaskAction -Execute ''powershell.exe'' -Argument ' + (Format-PowerShellString $ActionArguments) + ' -WorkingDirectory ' + (Format-PowerShellString $ProjectRoot)
    $Lines += '$DailyTrigger = New-ScheduledTaskTrigger -Daily -At ' + (Format-PowerShellString $RunTime)
    $Lines += '$RetryTrigger = New-ScheduledTaskTrigger -Daily -At ' + (Format-PowerShellString $RetryTime)
    $Lines += '$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User ' + (Format-PowerShellString $UserName)
    $Lines += '$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew'
    $Lines += '$Principal = New-ScheduledTaskPrincipal -UserId ' + (Format-PowerShellString $UserName) + ' -LogonType Interactive'
    $Lines += 'Register-ScheduledTask -TaskName ' + (Format-PowerShellString $TaskName) + ' -Action $Action -Trigger @($DailyTrigger, $RetryTrigger, $LogonTrigger) -Settings $Settings -Principal $Principal -Description ''Opens the AI Morning Wire in Claude Code each morning.'' -Force -ErrorAction Stop'
    return ($Lines -join "`n")
}

# ---- Run ----------------------------------------------------------------------------------------------------------------

if ($DryRun) {
    Write-Host 'DRY RUN: nothing below is written, registered or started.' -ForegroundColor Yellow
}

Write-Step 'Checking this PC'
if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    Stop-Setup 'this only runs on Windows. Fix: run it on a Windows PC.'
}
Write-Host 'Windows: ok'

# The run saves source files as work\<date>\sources\<story slug, up to 80 characters>\<file>, up to about 140 characters below
# the folder, and Windows refuses paths longer than 260 characters.
if ($ProjectRoot.Length -gt $MaxRootLength) {
    Stop-Setup "this folder's path is $($ProjectRoot.Length) characters long, and the morning run saves files up to about 140 characters deeper, past the 260-character path limit of Windows. Fix: move the folder to a path of at most $MaxRootLength characters (for example C:\ai-morning-wire), then run setup again from there."
}
Write-Host "Folder path: ok ($($ProjectRoot.Length) characters)"

$PythonInfo = Find-Python 'import sys; print(sys.executable); print(sys.version_info[0]); print(sys.version_info[1])'
$PythonPath = $PythonInfo.Path
Write-Host "Python $($PythonInfo.Version): $PythonPath"

$ClaudePath = $null
foreach ($ClaudeCandidate in @(Get-Command claude -CommandType Application -All -ErrorAction SilentlyContinue)) {
    if ($null -eq $ClaudePath -and (Test-Path -LiteralPath $ClaudeCandidate.Source)) {
        $ClaudePath = $ClaudeCandidate.Source
    }
}
if ($null -eq $ClaudePath) {
    Stop-Setup 'the claude command was not found. Fix: install Claude Code from https://claude.com/claude-code and make sure `claude` runs in a new PowerShell window, then run setup again.'
}
Write-Host "Claude Code: $ClaudePath"
Test-ClaudeLogin $ClaudePath

if (-not (Test-WindowsTerminal)) {
    Stop-Setup 'Windows Terminal (wt.exe) was not found at %LOCALAPPDATA%\Microsoft\WindowsApps\wt.exe, where the morning script starts it. Fix: install it with `winget install Microsoft.WindowsTerminal` or from the Microsoft Store, then run setup again.'
}
Write-Host 'Windows Terminal: ok'

$GitBashPath = Find-GitBash
if ($null -eq $GitBashPath) {
    Stop-Setup 'Git Bash was not found. The morning session runs its commands in Git Bash, because Claude Code''s PowerShell tool asks before every command on some PCs. Fix: install Git for Windows from https://git-scm.com/download/win, then run setup again in a new PowerShell window.'
}
Write-Host "Git Bash: $GitBashPath"

Write-Step 'A few questions'
# An existing config.toml supplies the default of every question and the task name; the public defaults apply without one.
$ExistingConfig = $null
$ExistingConfigError = ''
if (Test-Path -LiteralPath $ConfigPath) {
    $ExistingRead = Read-ConfigWithPython $PythonPath
    $ExistingConfig = $ExistingRead.Config
    $ExistingConfigError = $ExistingRead.Error
}
$DefaultTime = '08:00'
$DefaultClaudeAiPage = $false
$DefaultAlphaSignal = $false
$DefaultTelegram = $false
$DefaultBrowserFallback = $false
if ($null -ne $ExistingConfig) {
    Write-Host 'The existing config.toml supplies the default of each question.'
    $DefaultTime = [string]$ExistingConfig.time
    $DefaultClaudeAiPage = [bool]$ExistingConfig.claude_ai_page
    $DefaultAlphaSignal = [bool]$ExistingConfig.alphasignal
    $DefaultTelegram = [bool]$ExistingConfig.telegram
    $DefaultBrowserFallback = [bool]$ExistingConfig.browser_fallback
} elseif ($ExistingConfigError -ne '') {
    Write-Host "The existing config.toml does not load ($ExistingConfigError), so the questions start from the public defaults."
}
if ($TaskName -eq '') {
    $TaskName = $DefaultTaskName
    if ($null -ne $ExistingConfig -and ([string]$ExistingConfig.task_name).Trim() -ne '') {
        $TaskName = [string]$ExistingConfig.task_name
        Write-Host "Task name from config.toml: $TaskName"
    }
}
if ($Time -ne '') {
    if ($Time -notmatch $TimePattern) {
        Stop-Setup "-Time must look like 08:00 (24-hour HH:MM), not '$Time'."
    }
    $RunTime = $Time
} elseif ($Defaults) {
    $RunTime = $DefaultTime
    Write-Host "Time of day for the digest -> $DefaultTime (default)"
} else {
    $RunTime = ''
    while ($RunTime -eq '') {
        $Raw = Read-Host "What time should the digest run each day? 24-hour HH:MM [$DefaultTime]"
        if ($null -eq $Raw) { $Raw = '' }
        $Typed = $Raw.Trim()
        if ($Typed -eq '') { $Typed = $DefaultTime }
        if ($Typed -match $TimePattern) {
            $RunTime = $Typed
        } else {
            Write-Host 'Please type the time like 08:00 (24-hour, two digits each).'
        }
    }
}

$ClaudeAiPage = Ask-YesNo 'Also publish the digest as a private claude.ai page?' $DefaultClaudeAiPage

Write-AlphaSignalWarning
$UseAlphaSignal = Ask-YesNo 'Turn on AlphaSignal?' $DefaultAlphaSignal
Write-TelegramWarning
$UseTelegram = Ask-YesNo 'Turn on the Telegram channels?' $DefaultTelegram
Write-BrowserFallbackWarning
$UseBrowserFallback = Ask-YesNo 'Turn on the browser fallback?' $DefaultBrowserFallback
if ($UseBrowserFallback) {
    if (-not (Test-PlaywrightServer $ClaudePath)) {
        $UseBrowserFallback = $false
    }
}

Write-Step 'config.toml'
$ConfigLines = @(
    '# AI Morning Wire settings, written by setup.ps1. See config.example.toml for every key.',
    '# To change browser_fallback, claude_ai_page, python or time later, run setup.ps1 again (it updates the allow rules and the task).',
    ('time = ' + (Format-TomlString $RunTime)),
    'max_stories_per_day = 25',
    ('browser_fallback = ' + (Format-TomlBool $UseBrowserFallback)),
    ('claude_ai_page = ' + (Format-TomlBool $ClaudeAiPage)),
    ('python = ' + (Format-TomlString $PythonPath)),
    ('claude = ' + (Format-TomlString $ClaudePath)),
    ('task_name = ' + (Format-TomlString $TaskName)),
    '',
    '[sources]',
    'hacker_news = true',
    'daily_papers = true',
    ('alphasignal = ' + (Format-TomlBool $UseAlphaSignal)),
    ('telegram = ' + (Format-TomlBool $UseTelegram)),
    'telegram_channels = ["neuraldvig", "whackdoor"]',
    'hacker_news_max = 12',
    'daily_papers_max = 8'
)
$ConfigText = ($ConfigLines -join "`n") + "`n"
$WriteConfig = $true
if (Test-Path -LiteralPath $ConfigPath) {
    if ($Yes -or $DryRun) {
        $WriteConfig = $false
        Write-Host 'config.toml already exists. Replace it with these answers? -> no (-Yes and -DryRun never replace it; answer yes interactively, or delete the file, to replace it)'
    } else {
        $WriteConfig = Read-YesNo "config.toml already exists. Replace it with these answers?" $false $false
    }
    if (-not $WriteConfig) {
        Write-Host 'Keeping the existing config.toml.'
    }
}
if ($WriteConfig) {
    if ($DryRun) {
        Write-Host "dry run: would write $ConfigPath (UTF-8, no BOM):"
        Write-Host $ConfigText
    } else {
        Write-Utf8NoBom $ConfigPath $ConfigText
        Write-Host "Wrote $ConfigPath"
        $Check = Read-ConfigWithPython $PythonPath
        if ($Check.Exit -ne 0) {
            Stop-Setup "the config.toml just written does not load: $($Check.Error)"
        }
        Write-Host 'config.toml loads correctly.'
    }
} else {
    if ($null -eq $ExistingConfig) {
        Stop-Setup "the existing config.toml does not load: $ExistingConfigError  Fix: correct it, or run setup again and agree to replace it."
    }
    # A kept config.toml decides everything but the time, which the task's triggers must match.
    $ClaudeAiPage = [bool]$ExistingConfig.claude_ai_page
    $UseBrowserFallback = [bool]$ExistingConfig.browser_fallback
    Write-Host "The kept config.toml decides claude_ai_page ($(Format-TomlBool $ClaudeAiPage)) and browser_fallback ($(Format-TomlBool $UseBrowserFallback)); the other answers are not applied to it."
    Write-Host 'To change either, edit config.toml (or replace it) and run setup.ps1 again, so the allow rules follow.'
    if ($RunTime -ne [string]$ExistingConfig.time) {
        Write-Host "The kept config.toml has time = $(Format-TomlString ([string]$ExistingConfig.time)), but the task will run at $RunTime."
        if ($DryRun) {
            Write-Host "dry run: would rewrite only the time line of $ConfigPath to: time = $(Format-TomlString $RunTime)"
        } else {
            Set-ConfigTimeLine $ConfigPath $RunTime
            Write-Host "Rewrote only the time line of $ConfigPath to: time = $(Format-TomlString $RunTime)"
            $Check = Read-ConfigWithPython $PythonPath
            if ($Check.Exit -ne 0) {
                Stop-Setup "config.toml no longer loads after the time change: $($Check.Error)"
            }
        }
    } else {
        Write-Host "The task will run at the kept config.toml's time, $RunTime."
    }
    if ($DryRun) {
        Write-Host 'dry run: would keep the rest of the existing config.toml.'
    }
}

Write-Step '.claude\settings.local.json (permissions for the unattended morning session)'
# The runbook uses the python value of config.toml (py -3 when it is empty), so a kept config.toml decides the rules.
$RulePython = $PythonPath
if (-not $WriteConfig) {
    $RulePython = [string]$ExistingConfig.python
    if ($RulePython -eq '') {
        Write-Host 'The kept config.toml has no python value, so the rules use py -3, like the runbook.'
    } else {
        Write-Host "The rules use the python value of the kept config.toml: $RulePython"
    }
    if ($UseBrowserFallback -and $PlaywrightServerNames.Count -eq 0) {
        Write-Host 'The kept config.toml has browser_fallback on, so the rules cover the Playwright tools it uses.'
        [void](Test-PlaywrightServer $ClaudePath)
    }
}
# The session runs every command through the Bash tool (Git Bash); .claude\settings.json turns off the PowerShell tool.
# browser_source.py runs only when the browser fallback is on, so its rule and the browser and WebFetch rules depend on it.
$AllowRules = @()
foreach ($ScriptName in $PromptScripts) {
    if ($ScriptName -eq 'browser_source.py' -and -not $UseBrowserFallback) {
        continue
    }
    $AllowRules += Get-InterpreterRule $RulePython $ScriptName
}
# Only the browser tools the runbook names (step 2), never the server wildcard: it would also allow browser_run_code_unsafe,
# browser_file_upload, browser_click and the like in an unattended session that reads untrusted pages.
if ($UseBrowserFallback) {
    $AllowRules += 'WebFetch'
    foreach ($PlaywrightServerName in $PlaywrightServerNames) {
        foreach ($BrowserTool in $RunbookBrowserTools) {
            $AllowRules += 'mcp__' + (Get-McpRuleServerName $PlaywrightServerName) + '__' + $BrowserTool
        }
    }
}
# The Artifact tools and the two skills the runbook loads before a first page exist only for the claude.ai page. The deny rules
# for deleting, pinning and writing the page's database stay in the committed .claude\settings.json whatever this says.
if ($ClaudeAiPage) {
    foreach ($ArtifactRule in $ArtifactRules) {
        $AllowRules += $ArtifactRule
    }
}
# Rules setup owns are replaced, not accumulated: script rules for any interpreter spelling, and each optional group's rules when
# that feature is off. Everything else in an existing file is kept.
$ScriptNamePatterns = @()
foreach ($ScriptName in $PromptScripts) {
    $ScriptNamePatterns += [regex]::Escape($ScriptName.Substring(0, $ScriptName.Length - 3))
}
$OwnedRulePatterns = @('^(Bash|PowerShell)\(.*\b(' + ($ScriptNamePatterns -join '|') + ')\.py \*\)$')
if (-not $UseBrowserFallback) {
    $BrowserToolPatterns = @()
    foreach ($BrowserTool in $RunbookBrowserTools) {
        $BrowserToolPatterns += [regex]::Escape($BrowserTool)
    }
    $OwnedRulePatterns += '^WebFetch$'
    $OwnedRulePatterns += '^mcp__[A-Za-z0-9_-]+__(' + ($BrowserToolPatterns -join '|') + ')$'
}
if (-not $ClaudeAiPage) {
    $ArtifactRulePatterns = @()
    foreach ($ArtifactRule in $ArtifactRules) {
        $ArtifactRulePatterns += [regex]::Escape($ArtifactRule)
    }
    $OwnedRulePatterns += '^(' + ($ArtifactRulePatterns -join '|') + ')$'
}
$SettingsJson = Get-MergedSettingsJson $AllowRules $OwnedRulePatterns
if ($DryRun) {
    Write-Host "dry run: would write $SettingsPath (UTF-8, no BOM)$(if (Test-Path -LiteralPath $SettingsPath) { ', merged into the existing file' }):"
    Write-Host $SettingsJson
} else {
    New-Item -ItemType Directory -Force -Path $SettingsFolder | Out-Null
    Write-Utf8NoBom $SettingsPath ($SettingsJson + "`n")
    Write-Host "Wrote $SettingsPath"
}

Write-Step "Scheduled task '$TaskName'"
$MorningArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $MorningScript, '-Python', $PythonPath, '-Claude', $ClaudePath)
$RegisterCommand = Get-RegisterCommand $MorningArguments $RunTime
$RetryTimeText = Get-RetryTime $RunTime
if ($RetryTimeText -lt $RunTime) {
    Write-Host "The task opens Claude Code each day at $RunTime and at logon. A second trigger at $RetryTimeText ($RetryDelayMinutes minutes later, so at or just after midnight) only closes a run left open; it never starts a new day early."
} else {
    Write-Host "The task opens Claude Code each day at $RunTime, again at $RetryTimeText ($RetryDelayMinutes minutes later: a retry or the watchdog) and at logon."
}
Write-Host 'It runs only while you are logged on. This is the exact command:'
Write-Host ''
Write-Host $RegisterCommand
Write-Host ''
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $ExistingTask) {
    Write-Host "A task named '$TaskName' already exists; registering replaces it." -ForegroundColor Yellow
    $Register = Confirm-Step 'Replace the existing task?' $false
} else {
    $Register = Confirm-Step 'Register this task now?' $true
}
if ($DryRun) {
    Write-Host 'dry run: the task was not registered.'
} elseif ($Register) {
    try {
        & ([scriptblock]::Create($RegisterCommand)) | Out-Null
    } catch {
        if (Test-AccessDenied $_.Exception.Message) {
            Stop-Setup 'Windows refused to register the task (access denied). Logon triggers need administrator rights on some PCs. Fix: right-click PowerShell, choose Run as administrator, and run setup.ps1 again; your config.toml is already written and will be kept.'
        }
        Stop-Setup "the task could not be registered: $($_.Exception.Message)"
    }
    if ($null -eq (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Stop-Setup "Register-ScheduledTask finished but no task named '$TaskName' exists. Fix: run setup again as Administrator."
    }
    Write-Host "Registered '$TaskName'." -ForegroundColor Green
} else {
    Write-Host 'Skipped registering the task. Run setup again when you are ready.'
}

Write-Step 'First run (required)'
$FirstRunCommand = 'powershell.exe ' + (Format-ArgumentLine ($MorningArguments + @('-IgnoreTime')))
if ($DryRun -or $Register) {
    Write-Host 'The first run is required. Claude Code ignores the allow rules of this folder until you accept its "trust this folder"'
    Write-Host 'prompt once, so the schedule cannot work before that. A new Windows Terminal window opens and builds today''s digest:'
    Write-Host '  - watch that window and accept the "trust this folder" prompt once;'
    Write-Host '  - if you turned on the claude.ai page, approve the page''s database capability once on its first publish;'
    Write-Host '  - after that, the morning runs need no prompts.'
} else {
    Write-Host 'You skipped registering the task, but the first run is still required before the schedule can work: Claude Code'
    Write-Host 'ignores the allow rules of this folder until you accept its "trust this folder" prompt once. Run this command'
    Write-Host 'after you register the task (watch the new window, accept the prompt once, approve the claude.ai page''s database'
    Write-Host 'capability on its first publish if that page is on). It is also the command printed below.'
}
Write-Host 'This is the command:'
Write-Host $FirstRunCommand
if ($DryRun) {
    Write-Host 'dry run: the first run was not started.'
} elseif ($Register) {
    Write-Host ''
    & powershell.exe @MorningArguments -IgnoreTime
    Write-Host ''
    Write-Host "The first run finished with exit code $LASTEXITCODE (0 = a window opened or nothing was due)."
    $RunLog = Join-Path $ProjectRoot 'data\runs.log'
    $LastRunLine = ''
    if (Test-Path -LiteralPath $RunLog) {
        $LastRunLine = [string](Get-Content -LiteralPath $RunLog -Tail 1)
        Write-Host ('Last line of data\runs.log: ' + $LastRunLine)
    }
    if ($LastRunLine -notmatch 'morning task: opened the ') {
        Write-Host ''
        Write-Host 'No window opened (today may already be published, or a run is in progress), so this folder''s "trust this folder" prompt was' -ForegroundColor Yellow
        Write-Host 'not shown. Before the next scheduled morning, open a terminal in this folder, run `claude` once and accept the prompt.' -ForegroundColor Yellow
        Write-Host 'Until you do, Claude Code ignores the allow rules and the morning run stalls on permission prompts. This matters most' -ForegroundColor Yellow
        Write-Host 'after moving the folder: the trust is remembered per folder path.' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Setup finished. To remove the scheduled task later, run uninstall.ps1.' -ForegroundColor Green
