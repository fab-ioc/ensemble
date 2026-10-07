; Inno Setup script for the Windows installer (Ensemble-<version>-windows-x64-setup.exe).
; Installs for the current user only: no administrator rights needed.
;
;   iscc /DAppVersion=0.9.0 /DSourceDir=dist\Ensemble /DOutputDir=release packaging\ensemble.iss

#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Ensemble"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{6F0B7C2E-5D41-4E3A-9C8B-E7A1D2F4B183}
AppName=Ensemble
AppVersion={#AppVersion}
AppPublisher=Ensemble
AppPublisherURL=https://github.com/fab-ioc/ensemble
DefaultDirName={localappdata}\Programs\Ensemble
DefaultGroupName=Ensemble
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=Ensemble-{#AppVersion}-windows-x64-setup
SetupIconFile=..\static\icons\favicon.ico
UninstallDisplayIcon={app}\Ensemble.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; A running hub holds Ensemble.exe open: ask to close it first.
CloseApplications=yes
LicenseFile=..\LICENSE

[Tasks]
Name: "autostart"; Description: "Start Ensemble when I sign in"; GroupDescription: "Options:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Ensemble"; Filename: "{app}\Ensemble.exe"
Name: "{userprograms}\Uninstall Ensemble"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\Ensemble.exe"; Parameters: "--autostart on"; Flags: runhidden; Tasks: autostart
Filename: "{app}\Ensemble.exe"; Description: "Open Ensemble"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\Ensemble.exe"; Parameters: "--autostart off"; Flags: runhidden; RunOnceId: "autostart-off"

[UninstallDelete]
; What an update leaves next to the app (the old version kept for a rollback,
; a new one that did not start).
Type: filesandordirs; Name: "{app}\.previous"
Type: filesandordirs; Name: "{app}\.failed"
