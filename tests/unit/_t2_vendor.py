"""The T2 vendor cache of the ``sandbox_docker`` tests: npm installs it in the coding image.

One install for every fixture that needs a cache (``test_app_builder_engine``
``t2_vendor``, and ``test_app_builder_t2_containment`` ``vendor``). Each
fixture names its own packages.

The install writes into a host dir that the test process owns. On a GitHub
runner (uid 1001) a container user of 1000:1000 got EACCES, and the Docker
tests skipped, which the CI job counts as a failure. So the container runs as
the host's own uid and gid. Windows has no getuid, and Docker Desktop maps any
user onto the bind mount.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

#: The versions that production installs at /opt/acb/t2-vendor
#: (``scripts/vps_apply.sh``). The host build route runs with these, so the
#: import fence of task 2464 runs with them too.
PROD_T2_PACKAGES = ("react@18.3.1", "react-dom@18.3.1", "esbuild@0.24.0", "lucide-react@1.17.0")


def host_platform_args() -> tuple[str, ...]:
    """npm flags that install the optional binaries of THIS host.

    The container is Linux. A test that runs ``node build_t2.mjs`` on the host
    needs esbuild's binary for the host (``@esbuild/win32-x64`` on Windows),
    not the container's. npm 10 takes ``--os`` and ``--cpu`` for that.
    """
    npm_os = {"win32": "win32", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    npm_cpu = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    return (f"--os={npm_os}", f"--cpu={npm_cpu}")


def install_t2_vendor(
    image: str, vendor: Path, packages: tuple[str, ...], *extra: str,
) -> subprocess.CompletedProcess[str]:
    """Run ``npm install`` of *packages* into the host dir *vendor*, in *image*."""
    user = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") else "1000:1000"
    return subprocess.run(
        ["docker", "run", "--rm", "--user", user,
         "--mount", f"type=bind,source={vendor},target=/v",
         "-e", "HOME=/tmp", "-e", "npm_config_cache=/tmp/npm",
         image, "npm", "install", "--prefix", "/v", "--no-audit", "--no-fund",
         "--no-bin-links", "--no-package-lock", *extra, *packages],
        capture_output=True, text=True, timeout=600, check=False,
    )
