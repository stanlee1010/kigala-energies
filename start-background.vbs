' Starts the station system (port 8000) and the training portal (port 8001) with no windows.
' Each one restarts by itself if it stops. Windows runs this at login (Startup folder).
' Double-click it to start the servers by hand. Stop them with stop-servers.bat.
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run "cmd /c """ & folder & "\server-keepalive.bat"" serve.py 8000", 0, False
shell.Run "cmd /c """ & folder & "\server-keepalive.bat"" serve_demo.py 8001", 0, False
