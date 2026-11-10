' Starts Gemini Hotkey without a console window. Logs are written beside this file.
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "pythonw """ & dir & "\gemini_hotkey.py""", 0, False

