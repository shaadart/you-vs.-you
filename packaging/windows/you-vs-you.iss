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
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE

[Files]
Source: "..\..\dist\YouVsYou\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
; The window is drawn by Microsoft Edge WebView2 (built into Windows 11 and updated Windows
; 10). CI puts Microsoft's small bootstrapper here; it is run only when the runtime is missing.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{group}\You vs. You"; Filename: "{app}\YouVsYou.exe"
Name: "{autodesktop}\You vs. You"; Filename: "{app}\YouVsYou.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "A desktop shortcut"; Flags: unchecked

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft Edge WebView2 (needs the internet)..."; Check: NeedsWebView2; Flags: waituntilterminated
Filename: "{app}\YouVsYou.exe"; Description: "Open You vs. You"; Flags: nowait postinstall skipifsilent
; The user's games and Twin live in %LOCALAPPDATA%\pco and are NOT removed on uninstall.

[Code]
const WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasVersion(Root: Integer; Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := not (HasVersion(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Key)
    or HasVersion(HKLM, 'SOFTWARE\' + WebView2Key)
    or HasVersion(HKCU, 'Software\' + WebView2Key));
end;
