"""Spike: a tiny allow-list forward proxy (HTTP CONNECT + plain HTTP).

Runs inside its own container. The sandbox can reach ONLY this proxy (it sits
on an --internal network), so the allow-list is the whole egress policy.
A domain matches exactly or as a suffix: "pypi.org" allows "pypi.org" and
"files.pypi.org". Every decision is logged to stdout (docker logs).
"""

import asyncio
import os
from urllib.parse import urlsplit

ALLOW = [d.strip().lower() for d in os.environ.get("ALLOW_DOMAINS", "").split(",") if d.strip()]
PORT = 3128


def allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in ALLOW)


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(creader, cwriter):
    try:
        head = await asyncio.wait_for(creader.readuntil(b"\r\n\r\n"), timeout=15)
    except Exception:
        cwriter.close()
        return
    first = head.split(b"\r\n", 1)[0].decode("latin-1")
    try:
        method, target, _ = first.split(" ", 2)
    except ValueError:
        cwriter.close()
        return
    if method.upper() == "CONNECT":
        host, _, port = target.rpartition(":")
        port = int(port or 443)
    else:
        u = urlsplit(target)
        host, port = u.hostname or "", u.port or 80
    ok = allowed(host)
    print(f"{'ALLOW' if ok else 'DENY '} {method} {host}:{port}", flush=True)
    if not ok:
        cwriter.write(b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n"
                      b"egress denied by sandbox allow-list\n")
        await cwriter.drain()
        cwriter.close()
        return
    try:
        ureader, uwriter = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=15)
    except Exception as exc:
        cwriter.write(f"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n{exc}\n".encode())
        await cwriter.drain()
        cwriter.close()
        return
    if method.upper() == "CONNECT":
        cwriter.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await cwriter.drain()
    else:
        # Rewrite absolute-form request line to origin-form.
        u = urlsplit(target)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        rest = head.split(b"\r\n", 1)[1]
        uwriter.write(f"{method} {path} HTTP/1.1\r\n".encode() + rest)
        await uwriter.drain()
    await asyncio.gather(pipe(creader, uwriter), pipe(ureader, cwriter))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    print(f"egress proxy on :{PORT}, allow={ALLOW}", flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
