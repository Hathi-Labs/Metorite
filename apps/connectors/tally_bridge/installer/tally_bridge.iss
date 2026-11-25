; Inno Setup script of the Tally bridge (TLY-5 PR B, brief §6.7). One EXE
; installs the program as a Windows service that restarts itself, applies the
; firewall rule and the sign-in setting, and opens the setup page.
; Run it through installer/build.ps1. Signing waits for the owner's
; certificate (brief §10 question 8).

#define AppVersion GetEnv("TALLY_BRIDGE_VERSION")

[Setup]
AppId={{6C1E2A1D-5B0F-4E9A-9C3B-2F7D8A4E1B60}
AppName=Tally Bridge
AppVersion={#AppVersion}
DefaultDirName={autopf}\Tally Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=TallyBridgeSetup-{#AppVersion}
UninstallDisplayName=Tally Bridge
Compression=lzma2
SolidCompression=yes

[Dirs]
; The settings, the DPAPI hashes and the copy key. The service runs as
; LocalSystem and trusts these files, so [Code] ProtectDataDir makes the
; folder admins and SYSTEM only: Users may read, never create or change.
Name: "{commonappdata}\TallyBridge"

[Files]
Source: "..\dist\TallyBridge\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Run]
Filename: "{app}\tally_bridge_service.exe"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure TallyBridge reset= 86400 actions= restart/5000/restart/15000/restart/60000"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failureflag TallyBridge 1"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge.exe"; Parameters: "apply-windows --windows"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge_service.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge.exe"; Parameters: "setup-link --windows --wait --url-file ""{tmp}\setup-url.txt"""; Flags: runhidden waituntilterminated
; The page opens as the person who signed in, never in the administrator's browser.
Filename: "{code:SetupUrl}"; Flags: shellexec runasoriginaluser postinstall nowait skipifsilent; Description: "Open the setup page"; Check: HasSetupUrl

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\TallyBridge"

[UninstallRun]
Filename: "{app}\tally_bridge_service.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\tally_bridge_service.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{app}\tally_bridge.exe"; Parameters: "uninstall-windows --windows"; Flags: runhidden waituntilterminated; RunOnceId: "UndoChanges"

[Code]
const
  ServiceKey = 'SYSTEM\CurrentControlSet\Services\TallyBridge';

var
  WasInstalled: Boolean;

// An upgrade stops the running service first, so its files are free.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  WasInstalled := RegKeyExists(HKLM, ServiceKey);
  if WasInstalled then
    Exec(ExpandConstant('{sys}\sc.exe'), 'stop TallyBridge', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure Icacls(Params: String);
var
  Code: Integer;
begin
  if not Exec(ExpandConstant('{sys}\icacls.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    RaiseException('Could not set the rights of the data folder: icacls ' + Params);
end;

// Review finding 1. %ProgramData% lets every user create a folder or a file,
// and the LocalSystem service reads them. So: the administrators own every
// item, every explicit right goes, and the folder takes only these rights.
// A fresh install also deletes what a user put there before it.
procedure ProtectDataDir;
var
  Dir: String;
begin
  Dir := ExpandConstant('{commonappdata}\TallyBridge');
  Icacls('"' + Dir + '" /setowner *S-1-5-32-544 /T /C');
  Icacls('"' + Dir + '" /reset /T /C');
  Icacls('"' + Dir + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX');
  // An item made between /reset and /inheritance:r gets the same treatment.
  Icacls('"' + Dir + '" /setowner *S-1-5-32-544 /T /C');
  Icacls('"' + Dir + '\*" /reset /T /C');
  if not WasInstalled then
    DelTree(Dir + '\*', False, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    ProtectDataDir;
end;

// The address that the elevated setup-link step wrote. Inno deletes {tmp},
// and the address with it, when the setup ends.
// When the bridge did not start, setup-link wrote no address. The finish
// page then offers no empty "Open the setup page" step.
function HasSetupUrl: Boolean;
begin
  Result := FileExists(ExpandConstant('{tmp}\setup-url.txt'));
end;

function SetupUrl(Param: String): String;
var
  Url: AnsiString;
begin
  if LoadStringFromFile(ExpandConstant('{tmp}\setup-url.txt'), Url) then
    Result := Trim(String(Url))
  else
    Result := '';
end;
