[Setup]
AppName=ScreenBridge
AppVersion=2.1.0
AppPublisher=ScreenBridge
DefaultDirName={autopf}\ScreenBridge
DefaultGroupName=ScreenBridge
OutputDir=..\installer
OutputBaseFilename=ScreenBridge-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ScreenBridge.exe
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "..\dist\ScreenBridge.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ScreenBridge"; Filename: "{app}\ScreenBridge.exe"
Name: "{autodesktop}\ScreenBridge"; Filename: "{app}\ScreenBridge.exe"; Tasks: desktopicon

[Run]
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""ScreenBridge"" dir=in action=allow protocol=TCP localport=8765 profile=private"; Flags: runhidden
Filename: "{app}\ScreenBridge.exe"; Description: "Launch ScreenBridge"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""ScreenBridge"""; Flags: runhidden
