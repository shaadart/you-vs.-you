; Inno Setup script. Build: iscc /DAppVersion=0.1.0 packaging\windows\you-vs-you.iss
; Per-user install: no administrator prompt, nothing outside the user's own folders.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F1B7C1E-5B0A-4C79-9E52-1A0D3C6B8E41}
AppName=You vs. You
AppVersion={#AppVersion}
AppPublisher=Made by Shaad
AppPublisherURL=https://github.com/shaadart/you-vs.-you
DefaultDirName={autopf}\You vs. You
DefaultGroupName=You vs. You
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\icons\icon.ico
UninstallDisplayIcon={app}\YouVsYou.exe
OutputDir=..\..\installer
OutputBaseFilename=YouVsYou-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE

[Files]
Source: "..\..\dist\YouVsYou\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\You vs. You"; Filename: "{app}\YouVsYou.exe"
Name: "{autodesktop}\You vs. You"; Filename: "{app}\YouVsYou.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "A desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\YouVsYou.exe"; Description: "Open You vs. You"; Flags: nowait postinstall skipifsilent
; The user's games and Twin live in %LOCALAPPDATA%\pco and are NOT removed on uninstall.
