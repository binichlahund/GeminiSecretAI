' Legacy shortcut compatibility. Use Start GeminiSecretAI.vbs.
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)
sh.Run "wscript.exe " & q & dir & "\Start GeminiSecretAI.vbs" & q, 0, False
