"""The Windows service of the bridge (TLY-5 PR B, brief §6.7).

The service starts with Windows, before anyone signs in, and Windows starts it
again after a crash (``recovery_commands``). It runs the same server as
``python -m tally_bridge serve --windows``.

Two parts:

* ``ServiceRunner`` runs the server on a thread until a stop event is set. It
  is plain Python, so a test on Linux drives it.
* ``make_service_class`` builds the ``pywin32`` service class around it. It
  imports ``pywin32`` only when it runs, and only Windows has it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

SERVICE_NAME = "TallyBridge"
DISPLAY_NAME = "Tally Bridge"
DESCRIPTION = "Reads TallyPrime on this machine for the AI assistant of your company."

#: Restart after 5 s, then 15 s, then 60 s. The count resets after a day with
#: no crash. ``failureflag 1`` also restarts after a stop with an error code.
RESET_SECONDS = 86400
ACTIONS = "restart/5000/restart/15000/restart/60000"


def recovery_commands() -> list[list[str]]:
    return [
        ["sc", "failure", SERVICE_NAME, "reset=", str(RESET_SECONDS), "actions=", ACTIONS],
        ["sc", "failureflag", SERVICE_NAME, "1"],
    ]


Serve = Callable[[threading.Event], None]


class ServiceRunner:
    """Run *serve* on one thread until ``stop``. *serve* returns when the
    event is set. An error of *serve* is kept in ``error``, and ``wait``
    returns, so the service ends and Windows restarts it."""

    def __init__(self, serve: Serve) -> None:
        self._serve = serve
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self._serve(self._stop)
        except BaseException as exc:
            self.error = exc

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="tally-bridge", daemon=True)
            self._thread.start()

    def stop(self, timeout_s: float = 20.0) -> bool:
        """Ask the server to stop. True when it stopped within *timeout_s*."""
        self._stop.set()
        return self.wait(timeout_s)

    def wait(self, timeout_s: float | None = None) -> bool:
        if self._thread is None:
            return True
        self._thread.join(timeout_s)
        return not self._thread.is_alive()


def make_service_class(serve: Serve) -> Any:  # pragma: no cover - Windows
    """The ``pywin32`` service class. Windows only."""
    import servicemanager
    import win32service
    import win32serviceutil

    class TallyBridgeService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = DISPLAY_NAME
        _svc_description_ = DESCRIPTION

        def __init__(self, args: Any) -> None:
            super().__init__(args)
            self.runner = ServiceRunner(serve)

        def SvcStop(self) -> None:
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self.runner.stop()

        def SvcDoRun(self) -> None:
            servicemanager.LogInfoMsg(f"{DISPLAY_NAME} started.")
            self.runner.start()
            self.runner.wait()
            if self.runner.error is not None:
                servicemanager.LogErrorMsg(f"{DISPLAY_NAME} stopped: {self.runner.error!r}")
                raise SystemExit(1)

    return TallyBridgeService


def service_main(argv: list[str], serve: Serve) -> int:  # pragma: no cover - Windows
    """The entry of ``tally_bridge_service.exe``. With no argument, Windows
    starts the service. With one (``install``, ``remove``, ``start``,
    ``stop``), it manages the service."""
    import servicemanager
    import win32serviceutil

    cls = make_service_class(serve)
    if len(argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(cls)
        servicemanager.StartServiceCtrlDispatcher()
        return 0
    win32serviceutil.HandleCommandLine(cls, argv=argv)
    return 0
