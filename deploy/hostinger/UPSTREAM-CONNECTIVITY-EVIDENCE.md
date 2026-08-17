# Upstream connectivity fault — evidence pack

Written 2026-07-28. Paste the body below into a Hostinger support ticket.

> ## UPDATE 2026-07-28: this is an Airtel routing fault, and it is bigger than Metorite
>
> Confirmed by the client: the site loads fine over 5G mobile data and fails on
> Airtel fixed-line broadband. Follow-up testing from the Airtel connection:
>
> | Target | Prefix | Result |
> | --- | --- | --- |
> | `187.127.179.143` (Metorite VPS) | 187.127.179.0/24 | unreachable — trace dies at `182.79.20.133` (Airtel) |
> | `82.180.143.135` (**fracktal.in main website**) | 82.180.143.0/24 | unreachable — trace dies at `116.119.33.231` (Airtel) |
> | `hostinger.com` (CDN-fronted) | — | HTTP 200 |
>
> **Two unrelated Hostinger prefixes are both unreachable, and both traceroutes
> terminate inside Airtel's own network before reaching Hostinger.** So:
>
> - Requesting a different IPv4 for the VPS will NOT help — a completely
>   different Hostinger prefix is equally unreachable. Do not spend money or a
>   change window on this.
> - **The company's main website is affected too**, not just Metorite.
>   Establish blast radius first: have someone else on Airtel broadband (a
>   colleague, another office) load `https://fracktal.in`. If it fails for them
>   too, Airtel-connected customers cannot reach the company site, which is a
>   revenue issue and outranks everything else here.
> - **The primary ticket is now with Airtel, not Hostinger** — though Hostinger
>   should still be told their ranges are unreachable from AS24560.

The point of this document: **the VPS is not at fault and cannot be fixed from
inside.** Inbound packets from some networks never reach the network interface
at all. Everything below is measurement, not inference, so support can't
reasonably bounce it back as "check your firewall".

Re-collect any of it with:

```bash
gh workflow run vps-forensics.yml --ref main     # deep, read-only
gh workflow run vps-health.yml --ref main -f diagnose=true
```

---

## Ticket body

> **Subject:** VPS 1747539 (187.127.179.143) — inbound packets from some networks never reach the VM
>
> VPS `srv1747539.hstgr.cloud`, IPv4 `187.127.179.143`, KVM 2, Ubuntu 24.04.
>
> Since 2026-07-27 the server is intermittently unreachable from some networks
> while remaining fully reachable from others **at the same moment**. The VM
> itself is healthy throughout — it never reboots on its own, CPU sits at ~3%,
> and it keeps serving traffic to the networks that can reach it.
>
> I have ruled out the server side by measurement, not by inspection:
>
> - `ip -s link show eth0` → **RX errors 0, dropped 0, missed 0**. Nothing
>   arrives and is then discarded.
> - `iptables -L INPUT -v -n` → policy DROP counter at **48 packets / 4871
>   bytes** across 32 minutes of uptime, i.e. background scan noise. Dozens of
>   genuine connection attempts made from a blocked network during that exact
>   window do not appear in the counter at all — the SYNs never arrived.
> - `TcpExtListenDrops 2`, `SyncookiesSent 0`, `ListenOverflows 0`,
>   `TCPBacklogDrop 0`.
> - **No fail2ban, no CrowdSec, no ipset** are installed. Nothing on the host
>   bans IP addresses.
> - `ufw` is active and explicitly ALLOWs 22, 80, 443, 8080, 3001 from
>   Anywhere (v4 and v6).
> - Only `monarx-agent` and `qemu-guest-agent` run as additional services;
>   neither filters packets.
> - Listeners are correct and bound to all interfaces: `*:443` and `*:80`
>   (caddy), `0.0.0.0:8080` (uvicorn), `*:3001` (next-server), `0.0.0.0:22`
>   (sshd).
> - No Hostinger cloud firewall is attached (`firewall_group_id: null`).
>
> Observed simultaneously, which is the core of the problem:
>
> | Source network | Result |
> | --- | --- |
> | GitHub Actions runners (Azure) | SSH connects on first attempt; HTTPS returns 200 |
> | Client on Airtel Broadband, India (122.172.81.47) | 100% ICMP loss; TCP 22/80/443/8080 all time out (packets dropped, no RST) |
>
> A traceroute from the affected client stops at hop 4, `182.79.240.3`, inside
> Airtel's own network — the packets never reach Hostinger's edge.
>
> Also unexplained and possibly related: the VM was restarted twice on
> 2026-07-27 (04:11 and 11:41 UTC) without any action from us, and there was a
> ~1 GB incoming traffic spike around 06:15 UTC that day against a ~30 MB
> baseline.
>
> Questions:
> 1. Is `187.127.179.143` subject to any DDoS mitigation, null-routing, or
>    rate-limiting that would drop traffic selectively by source network?
> 2. Were the two reboots on 2026-07-27 initiated by Hostinger, and why?
> 3. Is there a known routing issue between Hostinger and Airtel (AS24560 /
>    AS9498) for this IP or its prefix?
> 4. If none of the above, can the VM be assigned a different IPv4 to test
>    whether the problem follows the address or the route?

---

## Ticket body — Airtel (this is the one that actually fixes it)

> **Subject:** Broadband cannot route to Hostinger network ranges — two
> separate prefixes unreachable, traceroute dies inside Airtel
>
> Connection: Airtel Broadband, Bengaluru (client IP 122.172.81.47).
>
> From this connection I cannot reach any Hostinger-hosted address, while the
> same destinations load normally over Airtel 5G mobile data and from servers
> outside India.
>
> - `187.127.179.143` — 100% ICMP loss; TCP 22/80/443 time out. Traceroute
>   reaches `182.79.20.133` (hop 3) then `182.79.240.3` (hop 4) and stops.
> - `82.180.143.135` — 100% ICMP loss. Traceroute reaches `182.79.20.201`
>   (hop 3) then `116.119.33.231` (hop 4) and stops.
> - `hostinger.com` (different network, CDN) — loads fine, HTTP 200.
>
> Both failing traceroutes terminate at Airtel-owned hops before leaving your
> network, and these are two unrelated destination prefixes, so this looks like
> a routing or peering fault between Airtel and Hostinger (AS47583 /
> AS200000-range hosting prefixes) rather than anything at the destination.
>
> The destination servers are confirmed healthy: they are reachable from other
> networks at the same moment, with zero inbound packet loss recorded at the
> server NIC.
>
> Please check routing from your network to `187.127.179.0/24` and
> `82.180.143.0/24`.

## What we are NOT doing, and why

**Not requesting a new VPS IP.** Ruled out by evidence: a completely different
Hostinger prefix (`82.180.143.135`) is equally unreachable from the affected
connection, so the fault does not follow the address.

**Not moving DNS to Cloudflare — for now.** It *would* fix this for every
visitor, by putting anycast in front of the unreachable origin, and that
argument got stronger once we learned the main website is affected too. But
`fracktal.in` carries Microsoft 365 mail (MX to
`fracktal-in.mail.protection.outlook.com`), four DKIM selector sets, Brevo,
Zoho Desk, Shiprocket, Atlassian and Google verification records — roughly two
dozen entries. A nameserver migration risks silently breaking company mail to
work around someone else's routing fault.

Reconsider it if either becomes true:
1. other Airtel-connected users also cannot reach `fracktal.in` (customer
   impact makes the migration worth the risk), or
2. Airtel does not fix the routing within a reasonable window.

If it is done, migrate mail-critical records first and verify MX + DKIM
resolve correctly from Cloudflare's nameservers *before* changing the
registrar's NS delegation.

**Not changing anything on the box.** There is nothing to change — see above.

## Confirming the diagnosis in one minute

Load `https://app.metorite.com` on a phone with wifi **off** (mobile
data). If it loads, the server is fine and the fault is the fixed-line ISP
path. That single test is worth more than any further server-side inspection.
