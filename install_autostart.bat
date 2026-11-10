@echo off
rem Creates a Windows Startup shortcut. Delete Gemini Hotkey.lnk in shell:startup to disable it.
set "VBS=%~dp0start_hidden.vbs"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Gemini Hotkey.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%VBS%\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"
echo Gemini Hotkey will now start when you sign in to Windows.
pause

