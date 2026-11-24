# Devices and Who's Online

## Devices

The **Devices** tab gives a device a friendly name, an optional group tag and
notes. The name shows on the Address Map's hover card and in Search (⌘K).

A device is found by the **hostname on its lease**, not by MAC, because a
device can change its MAC (private Wi-Fi addresses) and a docked laptop can
hold two leases at once. Pick the hostname from the list of names live leases
report. A reservation label never appears on a lease, so it isn't offered.

The IP and MAC columns come from the device's current lease, so they are
always current. A "—" means no lease carries that hostname right now: the
device is off, or it renamed itself.

## Who's online

Kea knows which addresses are leased, not which devices are powered on. A lease
can outlive its device by hours. KeaNexus answers "is it on right now?" by
sending an ARP request to every address in the subnet. A device on the same
network segment can't ignore ARP and stay reachable, so a reply proves the
device is there. No reply proves nothing, which is why the screens say
"no answer" and never "offline".

A sweep runs in the background every 5 minutes, and on demand from the Leases
tab's **Check who's online** button. The last result is saved, so a restart
shows it with its real age ("swept 12m ago") rather than nothing.

The **Online** columns on Leases, Reservations and Devices, the map's online
dots and the Overview's Online figure all read the same sweep.

## When the sweep fails

ARP only reaches the local network segment, and sending it needs a raw socket.
The KeaNexus container therefore runs with **host networking** and the
**NET_RAW** capability (see `docker-compose.yml`). If either is missing, the
Leases tab shows *"The sweep couldn't run"* with the reason:

| Message says | Fix |
| --- | --- |
| no permission to send raw packets | Add `cap_add: [NET_RAW]` to the `keanexus` service and recreate it. |
| No such device / interface not found | The host has several interfaces and the wrong one was picked. Set `ARP_INTERFACE=eth0` (the LAN one) in `.env`. |
| Everything says "no answer" | The container isn't on the LAN segment. Check `network_mode: host` is set. |

The background sweep is on by default. Set `KEANEXUS_WEB_LIVENESS_SWEEP=off` in
`.env` to stop it, for example when running KeaNexus on a laptop. The interval
is `LIVENESS_SWEEP_INTERVAL_SECONDS` (default 300, minimum 30).
