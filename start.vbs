Option Explicit
Dim files, shell, folder, python, app
Set files = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = files.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
app = folder & "\app.py"
If Not files.FileExists(python) Then
    MsgBox "Project Python environment is missing. Please see README.md.", 48, "Codex Usage Note"
    WScript.Quit 1
End If
shell.CurrentDirectory = folder
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & app & Chr(34), 1, False
