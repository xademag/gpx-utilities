; GPX Utility — Inno Setup 6 installer script
;
; Prerequisites
; -------------
;   1. Run  build_portable.py  first to populate  dist\GPX Utility\
;   2. Install Inno Setup 6  from  https://jrsoftware.org/isinfo.php
;
; Build
; -----
;   GUI:  Open this file in the Inno Setup Compiler and press F9
;   CLI:  iscc installer\gpx_utility.iss
;
; Output
; ------
;   dist\gpx-utility-setup.exe

#define AppName      "GPX Utility"
#define AppVersion   "1.0"
#define AppPublisher "GPX Utility"
#define AppExeName   "run.vbs"
#define SourceDir    "..\dist\GPX Utility"

[Setup]
; Unique GUID — regenerate with Tools > Generate GUID if you fork this project
AppId={{3F8A2D1E-B7C4-4F9E-A2D3-8E6F1C5B4A2D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=gpx-utility-setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 64-bit Windows only (matches the embeddable Python amd64 build)
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Require Windows 10+
MinVersion=10.0.17763
; Don't require admin rights — install per-user if not elevated
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; Show license/readme before install
;InfoBeforeFile=..\README.txt   ; uncomment to show README in wizard

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Embedded Python runtime + site-packages
Source: "{#SourceDir}\python\*"; \
    DestDir: "{app}\python"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; Application source + assets
Source: "{#SourceDir}\app\*"; \
    DestDir: "{app}\app"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; Launchers and docs
Source: "{#SourceDir}\GPX Utility.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\run.vbs";          DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.txt";       DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
; Start Menu
Name: "{group}\{#AppName}"; \
    Filename: "{app}\run.vbs"; \
    Comment: "Open GPX Utility"

Name: "{group}\Uninstall {#AppName}"; \
    Filename: "{uninstallexe}"

; Desktop (optional, user must tick the checkbox)
Name: "{autodesktop}\{#AppName}"; \
    Filename: "{app}\run.vbs"; \
    Comment: "Open GPX Utility"; \
    Tasks: desktopicon

[Run]
; Offer to launch after install
Filename: "{app}\run.vbs"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Remove settings file written by the app at runtime
Type: files; Name: "{app}\app\settings.json"
; Remove the entire install folder (catches any files created at runtime)
Type: dirifempty; Name: "{app}"
