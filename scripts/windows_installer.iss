#ifndef ReleaseVersion
  #error ReleaseVersion is required
#endif
#ifndef PayloadDirectory
  #error PayloadDirectory is required
#endif

[Setup]
AppId=RuuviHub.User
AppName=RuuviHub
AppVersion={#ReleaseVersion}
DefaultDirName={localappdata}\Programs\RuuviHub
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
UsePreviousAppDir=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
OutputBaseFilename=ruuvihub-{#ReleaseVersion}-windows-x64
Uninstallable=not IsUpdate
CloseApplications=no
Compression=lzma2
SolidCompression=yes

[Files]
Source: "{#PayloadDirectory}\*"; DestDir: "{tmp}\payload"; Flags: dontcopy noencryption recursesubdirs
Source: "windows_update.cmd"; DestDir: "{app}"; DestName: "ruuvihub-update.cmd"; Check: not IsUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}\releases"
Type: files; Name: "{app}\switch.json"
Type: files; Name: "{app}\update.lock"
Type: dirifempty; Name: "{app}"

[Code]
function CurrentVersionParameter: String;
begin
  Result := ExpandConstant('{param:EXPECTEDCURRENT|}');
end;

function IsUpdate: Boolean;
begin
  Result := CurrentVersionParameter <> '';
end;

function AppDirectory: String;
begin
  Result := ExpandConstant('{localappdata}\Programs\RuuviHub');
end;

function DataDirectory: String;
begin
  Result := ExpandConstant('{localappdata}\RuuviHub');
end;

function RunHelper(const ReleaseDir, Params: String; var ExitCode: Integer): Boolean;
begin
  ExitCode := -1;
  Result := Exec(ReleaseDir + '\runtime\python.exe',
    '-I -B "' + ReleaseDir + '\setup.py" ' + Params,
    ReleaseDir, SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
  if not Result then
    Log('RuuviHub setup helper failed with code ' + IntToStr(ExitCode));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Payload, Params: String;
  I: Integer;
begin
  if CompareText(ExpandConstant('{app}'), AppDirectory) <> 0 then
  begin
    Result := 'RuuviHub uses its fixed per-user installation directory.';
    Exit;
  end;
  for I := 1 to Length(CurrentVersionParameter) do
    if Pos(CurrentVersionParameter[I], 'v0123456789.') = 0 then
    begin
      Result := 'Invalid version supplied by the updater.';
      Exit;
    end;
  Payload := ExpandConstant('{tmp}\payload');
  ExtractTemporaryFiles('{tmp}\payload\*');
  Params := 'verify --version "{#ReleaseVersion}" --installer "' + ExpandConstant('{srcexe}') + '"';
  if not RunHelper(Payload, Params, ExitCode) then
    Result := 'RuuviHub could not verify this installer. Check your connection and download setup from the official GitHub release.'
  else
    Result := '';
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    'RuuviHub runs in your Windows user session. Scanning pauses when you sign out.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    Params := 'install --version "{#ReleaseVersion}" --base-dir "' + AppDirectory +
      '" --data-dir "' + DataDirectory + '"';
    if IsUpdate then
      Params := Params + ' --expected-current "' + CurrentVersionParameter + '"';
    if not RunHelper(ExpandConstant('{tmp}\payload'), Params, ExitCode) then
      RaiseException('RuuviHub could not activate the release. Persistent data was not removed.');
  end;
end;

function InitializeUninstall: Boolean;
var
  Files: TFindRec;
  Path, Candidate: String;
  ExitCode: Integer;
begin
  Result := False;
  Path := AppDirectory + '\releases\';
  if FindFirst(Path + 'v*', Files) then
  begin
    try
      repeat
        Candidate := Path + Files.Name;
        if FileExists(Candidate + '\setup.py') and FileExists(Candidate + '\runtime\python.exe') then
        begin
          Result := RunHelper(Candidate, 'uninstall --base-dir "' + AppDirectory +
            '" --data-dir "' + DataDirectory + '"', ExitCode);
          Break;
        end;
      until not FindNext(Files);
    finally
      FindClose(Files);
    end;
  end
  else
    Result := True;
  if not Result then
    SuppressibleMsgBox('Could not stop the RuuviHub task. No files have been removed.',
      mbError, MB_OK, IDOK);
end;
