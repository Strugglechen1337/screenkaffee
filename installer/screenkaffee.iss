#define AppIdValue "{{EA3D4BD5-CC56-41AF-87DE-3C0C5A17B724}"
[Setup]
AppId={#AppIdValue}
AppName=screenkaffee
AppVersion={#AppVersion}
AppPublisher=screenkaffee contributors
DefaultDirName={localappdata}\Programs\screenkaffee
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
DisableProgramGroupPage=yes
DefaultGroupName=screenkaffee
ArchitecturesAllowed={#Architecture}
ArchitecturesInstallIn64BitMode={#Architecture}
OutputDir={#OutputDir}
OutputBaseFilename=screenkaffee-Setup-Windows-{#Variant}
Compression=lzma2/fast
SolidCompression=yes
UninstallDisplayIcon={app}\screenkaffee.exe
CloseApplications=yes
CloseApplicationsFilter=screenkaffee.exe

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "data\*,_update\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\screenkaffee"; Filename: "{app}\screenkaffee.exe"
Name: "{autodesktop}\screenkaffee"; Filename: "{app}\screenkaffee.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\screenkaffee.exe"; Description: "{cm:LaunchProgram,screenkaffee}"; Flags: nowait postinstall skipifsilent

[CustomMessages]
german.DeleteData=Aufnahmen und Einstellungen ebenfalls löschen?
english.DeleteData=Also delete recordings and settings?

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  FindRec: TFindRec;
  Root, Name: String;
  DeleteData: Boolean;
begin
  if CurUninstallStep = usUninstall then begin
    Root := ExpandConstant('{app}');
    DeleteData := False;
    if not UninstallSilent and DirExists(Root + '\data') then
      DeleteData := MsgBox(ExpandConstant('{cm:DeleteData}'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if FindFirst(Root + '\*', FindRec) then begin
      try
        repeat
          Name := FindRec.Name;
          if (Name <> '.') and (Name <> '..') and
             ((CompareText(Name, 'data') <> 0) or DeleteData) and
             (CompareText(Copy(Name, 1, 5), 'unins') <> 0) then
            if FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0 then
              DelTree(Root + '\' + Name, True, True, True)
            else
              DeleteFile(Root + '\' + Name);
        until not FindNext(FindRec);
      finally
        FindClose(FindRec);
      end;
    end;
  end;
end;


