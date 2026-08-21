' Lanza watcher.py sin ventana visible. Ponlo en Inicio para que arranque solo.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
' pythonw = interprete sin consola
WshShell.Run "pythonw.exe """ & scriptDir & "\watcher.py""", 0, False
