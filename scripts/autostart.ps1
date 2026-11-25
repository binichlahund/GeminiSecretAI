param([string]$StartupFolder = [Environment]::GetFolderPath('Startup'))
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$launcherPath = Join-Path $projectRoot 'Start GeminiSecretAI.vbs'
if (-not (Test-Path -LiteralPath $launcherPath)) { throw 'The main launcher is missing.' }
$shortcutPath = Join-Path $StartupFolder 'GeminiSecretAI.lnk'
$shellObject = New-Object -ComObject WScript.Shell
$legacyPath = Join-Path $StartupFolder 'Gemini Hotkey.lnk'
if (Test-Path -LiteralPath $legacyPath) {
    $legacyShortcut = $shellObject.CreateShortcut($legacyPath)
    if ($legacyShortcut.Arguments.Contains($projectRoot)) { $shortcutPath = $legacyPath }
}
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
$shortcut.Arguments = '"' + $launcherPath + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Start GeminiSecretAI'
$shortcut.Save()
