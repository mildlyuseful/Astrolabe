; SPDX-FileCopyrightText: 2026 Dylan Lee
; SPDX-License-Identifier: Apache-2.0

; All product identity is supplied by tools/build_release.ps1 from
; trackball_daemon/product.py. The installer script refuses to compile without those values so it
; cannot quietly become a second authority for upgrade identity, paths, or artifact names.

#ifndef ProductName
  #error ProductName must be supplied by the release build
#endif
#ifndef Publisher
  #error Publisher must be supplied by the release build
#endif
#ifndef Version
  #error Version must be supplied by the release build
#endif
#ifndef NumericVersion
  #error NumericVersion must be supplied by the release build
#endif
#ifndef AppId
  #error AppId must be supplied by the release build
#endif
#ifndef InstallDirectory
  #error InstallDirectory must be supplied by the release build
#endif
#ifndef StartMenuFolder
  #error StartMenuFolder must be supplied by the release build
#endif
#ifndef ExecutableName
  #error ExecutableName must be supplied by the release build
#endif
#ifndef InstallerBaseName
  #error InstallerBaseName must be supplied by the release build
#endif
#ifndef SourceDirectory
  #error SourceDirectory must be supplied by the release build
#endif
#ifndef OutputDirectory
  #error OutputDirectory must be supplied by the release build
#endif
#ifndef ConfigDirectory
  #error ConfigDirectory must be supplied by the release build
#endif
#ifndef CurrentMutex
  #error CurrentMutex must be supplied by the release build
#endif
#ifndef LegacyMutex
  #error LegacyMutex must be supplied by the release build
#endif
#ifndef LicenseFile
  #error LicenseFile must be supplied by the release build
#endif

[Setup]
AppId={#AppId}
AppName={#ProductName}
AppVersion={#Version}
AppVerName={#ProductName} {#Version}
AppPublisher={#Publisher}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#ProductName} per-user installer
VersionInfoProductName={#ProductName}
VersionInfoProductVersion={#NumericVersion}
VersionInfoVersion={#NumericVersion}
DefaultDirName={localappdata}\{#InstallDirectory}
DefaultGroupName={#StartMenuFolder}
DisableProgramGroupPage=yes
OutputDir={#OutputDirectory}
OutputBaseFilename={#InstallerBaseName}
LicenseFile={#LicenseFile}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
Uninstallable=yes
UninstallDisplayIcon={app}\{#ExecutableName}
CloseApplications=yes
RestartApplications=no
AppMutex={#CurrentMutex},{#LegacyMutex}
UsePreviousAppDir=yes
ChangesAssociations=no
ChangesEnvironment=no
SetupLogging=yes

[Files]
Source: "{#SourceDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\{#ExecutableName}"

[Run]
Filename: "{app}\{#ExecutableName}"; Description: "Launch {#ProductName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigPath: String;
begin
  if (CurUninstallStep = usPostUninstall) and (not UninstallSilent) then
  begin
    ConfigPath := ExpandConstant('{userappdata}\{#ConfigDirectory}');
    if MsgBox(
      'Remove Astrolabe settings, profiles, logs, and the local Onshape certificate from:' +
      '' + #13#10 + ConfigPath + '?' + #13#10#13#10 +
      'Choose No to preserve them for an upgrade or reinstall.',
      mbConfirmation, MB_YESNO) = IDYES then
    begin
      if not DelTree(ConfigPath, True, True, True) then
        MsgBox('Some user data could not be removed. It remains at:' + #13#10 + ConfigPath,
          mbError, MB_OK);
    end;
  end;
end;
