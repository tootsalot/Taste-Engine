; Inno Setup script for the Windows installer (Inno Setup 6.7 or later).
; Built by scripts/build_release.py, which passes:
;   /DAppVersion=0.2.0  /DAppFolder=<the PyInstaller app folder>
;   /DOutputDir=<dist>  /DOutputName=<setup file name without .exe>
;
; Per user, no admin prompt: installs to %LOCALAPPDATA%\Programs\Taste Engine. Profiles
; and settings live in %LOCALAPPDATA%\taste-engine, outside the install folder, so
; updating or repairing never touches them. Uninstall deletes them only when asked.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z (scripts/build_release.py does)
#endif
#ifndef AppFolder
  #error Pass /DAppFolder=<the PyInstaller app folder>
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef OutputName
  #define OutputName "taste-engine-" + AppVersion + "-windows-x64-setup"
#endif

#define AppName "Taste Engine"
#define AppExe "taste-engine.exe"
; Never change this: updates, repairs, and uninstall find the installed copy by it.
#define AppGuid "3C5D937C-337E-45F7-AB56-E60E3A811924"
#define UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\{" + AppGuid + "}_is1"
#define DataFolder "{localappdata}\taste-engine"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Taste Engine
AppPublisherURL=https://github.com/tootsalot/Taste-Engine
AppSupportURL=https://github.com/tootsalot/Taste-Engine/issues
AppUpdatesURL=https://github.com/tootsalot/Taste-Engine/releases
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableWelcomePage=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=taste-engine.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Asks to close a running Taste Engine before replacing or removing its files.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startmenuicon"; Description: "Create a Start menu shortcut"; GroupDescription: "Shortcuts:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; PyInstaller's file list changes between builds, and Inno never removes files a newer
; version no longer ships. Clearing the bundle first keeps old Qt plugins from lingering.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#AppFolder}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: startmenuicon
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  InstalledVersion: String;  // '' when Taste Engine isn't installed for this user
  UninstallCommand: String;
  MaintenancePage: TInputOptionWizardPage;
  Leaving: Boolean;  // closing setup on purpose, so don't ask "Exit Setup?"

// The next number in a dotted version, removed from the front of S.
function TakeVersionPart(var S: String): Integer;
var
  Dot: Integer;
begin
  Dot := Pos('.', S);
  if Dot = 0 then
  begin
    Result := StrToIntDef(S, 0);
    S := '';
  end
  else
  begin
    Result := StrToIntDef(Copy(S, 1, Dot - 1), 0);
    S := Copy(S, Dot + 1, Length(S));
  end;
end;

// Less than 0 if A is older than B, 0 if they're the same, more than 0 if A is newer.
function CompareVersions(A, B: String): Integer;
var
  I, PartA, PartB: Integer;
begin
  Result := 0;
  for I := 1 to 4 do
  begin
    PartA := TakeVersionPart(A);
    PartB := TakeVersionPart(B);
    if PartA <> PartB then
    begin
      Result := PartA - PartB;
      Exit;
    end;
  end;
end;

function IsInstalled: Boolean;
begin
  Result := InstalledVersion <> '';
end;

function InitializeSetup: Boolean;
begin
  InstalledVersion := '';
  UninstallCommand := '';
  Leaving := False;
  if not RegQueryStringValue(HKCU, '{#UninstallKey}', 'DisplayVersion', InstalledVersion) then
    InstalledVersion := ''
  else if not RegQueryStringValue(HKCU, '{#UninstallKey}', 'UninstallString', UninstallCommand) then
    UninstallCommand := '';
  Result := True;
end;

procedure InitializeWizard;
var
  Change: Integer;
  Action, Note: String;
begin
  if not IsInstalled then
    Exit;
  Change := CompareVersions('{#AppVersion}', InstalledVersion);
  Note := '';
  if Change > 0 then
    Action := 'Update to {#AppVersion}'
  else if Change = 0 then
    Action := 'Repair {#AppVersion}'
  else
  begin
    Action := 'Reinstall {#AppVersion}';
    Note := 'This setup is older than the installed version. Profiles a newer version '
      + 'has opened may not work right in it.';
  end;
  MaintenancePage := CreateInputOptionPage(wpWelcome,
    '{#AppName} is already installed',
    'Version ' + InstalledVersion + ' is on this computer. What would you like to do?',
    Note, True, False);
  MaintenancePage.Add(Action);
  MaintenancePage.Add('Uninstall {#AppName}');
  MaintenancePage.Values[0] := True;
end;

// Updates, repairs, and reinstalls keep the license and shortcut choices from last time.
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsInstalled then
    Result := (PageID = wpLicense) or (PageID = wpSelectTasks);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if not IsInstalled then
    Exit;
  if (CurPageID = MaintenancePage.ID) and MaintenancePage.Values[1] then
  begin
    // Hand over to the installed uninstaller (it asks about the data), then close setup.
    if (UninstallCommand = '') or
       not Exec(RemoveQuotes(UninstallCommand), '', '', SW_SHOW, ewNoWait, ResultCode) then
    begin
      MsgBox('The uninstaller couldn''t be started. Uninstall {#AppName} from Settings > '
        + 'Apps instead.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    Leaving := True;
    Result := False;
    WizardForm.Close;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if Leaving then
    Confirm := False;
end;

// The uninstall question. Unticked by default: profiles stay unless asked.
function AskToDeleteData: Boolean;
var
  Form: TSetupForm;
  Box: TNewCheckBox;
  Note: TNewStaticText;
  Button: TNewButton;
begin
  Form := CreateCustomForm(ScaleX(440), ScaleY(150), False, False);
  try
    Form.Caption := 'Uninstall {#AppName}';
    Box := TNewCheckBox.Create(Form);
    Box.Parent := Form;
    Box.Left := ScaleX(16);
    Box.Top := ScaleY(18);
    Box.Width := Form.ClientWidth - ScaleX(32);
    Box.Height := ScaleY(20);
    Box.Caption := 'Also delete my profiles, settings, and saved API keys';
    Box.Checked := False;
    Note := TNewStaticText.Create(Form);
    Note.Parent := Form;
    Note.Left := ScaleX(34);
    Note.Top := ScaleY(44);
    Note.Width := Form.ClientWidth - ScaleX(50);
    Note.WordWrap := True;
    Note.Caption := 'They''re in ' + ExpandConstant('{#DataFolder}') + '. Left unticked, '
      + 'they stay, and {#AppName} finds them if you install it again.';
    Button := TNewButton.Create(Form);
    Button.Parent := Form;
    Button.Caption := 'Continue';
    Button.ModalResult := mrOk;
    Button.Default := True;
    Button.Width := ScaleX(90);
    Button.Height := ScaleY(26);
    Button.Left := Form.ClientWidth - Button.Width - ScaleX(16);
    Button.Top := Form.ClientHeight - Button.Height - ScaleY(14);
    Form.ActiveControl := Button;
    Form.FlipAndCenterIfNeeded(True, UninstallProgressForm, False);
    Result := (Form.ShowModal = mrOk) and Box.Checked;
  finally
    Form.Free;
  end;
end;

// The app deletes its own keys (it knows how they're named in Credential Manager), then
// the data folder. If it can't run, the folder still goes; the keys may stay.
procedure DeleteData;
var
  ResultCode: Integer;
  Folder: String;
begin
  Folder := ExpandConstant('{#DataFolder}');
  if not Exec(ExpandConstant('{app}\{#AppExe}'), '--delete-all-data', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    DelTree(Folder, True, True, True);
  if DirExists(Folder) then
    MsgBox('Some of your data couldn''t be deleted. You can delete this folder yourself:'
      + #13#10 + Folder, mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  // Before the files go, while the app is still there to delete its keys. A silent
  // uninstall never deletes data.
  if (CurUninstallStep = usUninstall) and not UninstallSilent then
    if AskToDeleteData then
      DeleteData;
end;
