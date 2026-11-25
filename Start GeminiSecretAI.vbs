' Everyday launcher. Install once with Install.bat.
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
folder = fs.GetParentFolderName(WScript.ScriptFullName)
launcher = fs.BuildPath(folder, "scripts\launch.pyw")
If Not fs.FileExists(launcher) Then
  MsgBox "Extract the whole GeminiSecretAI folder before starting it.", 16, "GeminiSecretAI"
  WScript.Quit 1
End If
If WScript.Arguments.Named.Exists("check") Then
  WScript.Echo launcher
  WScript.Quit 0
End If
q = Chr(34)
sh.Run "pythonw " & q & launcher & q, 0, False
