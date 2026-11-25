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
; The settings, the DPAPI hashes and the copy key. Only the service and the
; administrators write here.
Name: "{commonappdata}\TallyBridge"; Permissions: admins-full system-full

[Files]
Source: "..\dist\TallyBridge\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Run]
Filename: "{app}\tally_bridge_service.exe"; Parameters: "--startup auto install"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure TallyBridge reset= 86400 actions= restart/5000/restart/15000/restart/60000"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failureflag TallyBridge 1"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge.exe"; Parameters: "apply-windows --windows"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge_service.exe"; Parameters: "start"; Flags: runhidden waituntilterminated
Filename: "{app}\tally_bridge.exe"; Parameters: "setup-link --windows --open"; Flags: runhidden nowait postinstall; Description: "Open the setup page"

[UninstallRun]
Filename: "{app}\tally_bridge_service.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\tally_bridge_service.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{app}\tally_bridge.exe"; Parameters: "uninstall-windows --windows"; Flags: runhidden waituntilterminated; RunOnceId: "UndoChanges"
