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

// Review finding 1. %ProgramData% lets every user create a folder or a file,
// and the LocalSystem service trusts what it reads there. PrepareToInstall
// runs before [Files] and before every [Run] step, and an error text that it
// returns cancels the setup. So the folder is safe before anything reads it.

function Icacls(Params: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\icacls.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

function HasChildren(Dir: String): Boolean;
var
  Find: TFindRec;
begin
  Result := False;
  if FindFirst(Dir + '\*', Find) then
  begin
    try
      repeat
        if (Find.Name <> '.') and (Find.Name <> '..') then
          Result := True;
      until Result or not FindNext(Find);
    finally
      FindClose(Find);
    end;
  end;
end;

// The administrators own every item, explicit rights go, inheritance stops,
// and only SYSTEM and the administrators may write. Users may read. A fresh
// install deletes what a user put there before it.
function ProtectDataDir(Fresh: Boolean): String;
var
  Dir: String;
begin
  Result := '';
  Dir := ExpandConstant('{commonappdata}\TallyBridge');
  if not ForceDirectories(Dir) then
    Result := 'Could not make the data folder ' + Dir
  else if not Icacls('"' + Dir + '" /setowner *S-1-5-32-544 /T /C') then
    Result := 'Could not take the ownership of ' + Dir
  else if not Icacls('"' + Dir + '" /reset /T /C') then
    Result := 'Could not reset the rights of ' + Dir
  else if not Icacls('"' + Dir + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F *S-1-5-32-545:(OI)(CI)RX') then
    Result := 'Could not set the rights of ' + Dir
  else if Fresh then
    DelTree(Dir + '\*', False, True, True)
  // An item made between /reset and /inheritance:r gets the same treatment.
  else if HasChildren(Dir) and not (Icacls('"' + Dir + '" /setowner *S-1-5-32-544 /T /C') and Icacls('"' + Dir + '\*" /reset /T /C')) then
    Result := 'Could not reset the rights of the files in ' + Dir;
end;

// An upgrade stops the service first (net stop waits until it has stopped),
// so its files are free. Then the data folder is made safe.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
  WasInstalled: Boolean;
begin
  WasInstalled := RegKeyExists(HKLM, ServiceKey);
  if WasInstalled then
    Exec(ExpandConstant('{sys}\net.exe'), 'stop TallyBridge', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := ProtectDataDir(not WasInstalled);
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
