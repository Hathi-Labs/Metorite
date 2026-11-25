# PyInstaller spec of the Tally bridge (TLY-5 PR B). One folder, two programs:
# tally_bridge.exe (the command line) and tally_bridge_service.exe (the
# Windows service). Python and every library are bundled (brief §6.7 item 3).
# Run it through installer/build.ps1.
from pathlib import Path

ROOT = Path(SPECPATH).parent
STATIC = (str(ROOT / "tally_bridge" / "setup" / "static"), "tally_bridge/setup/static")
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

cli = Analysis(["cli_entry.py"], pathex=[str(ROOT)], datas=[STATIC], hiddenimports=HIDDEN)
svc = Analysis(["service_entry.py"], pathex=[str(ROOT)], datas=[STATIC],
               hiddenimports=HIDDEN + ["win32timezone"])

cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True,
              name="tally_bridge", console=True)
svc_exe = EXE(PYZ(svc.pure), svc.scripts, [], exclude_binaries=True,
              name="tally_bridge_service", console=True)

COLLECT(cli_exe, cli.binaries, cli.datas, svc_exe, svc.binaries, svc.datas,
        name="TallyBridge")
