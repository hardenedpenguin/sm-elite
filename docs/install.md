# Install on a node

On an AllStarLink 3 node (Debian 12 or 13), add the [hardenedpenguin apt repository](https://hardenedpenguin.github.io/hardenedpenguin-apt/) once, then install:

```bash
cd /tmp
curl -fsSLO https://hardenedpenguin.github.io/hardenedpenguin-apt/pool/main/h/hardenedpenguin-archive-keyring/hardenedpenguin-archive-keyring_1.2_all.deb
sudo apt install ./hardenedpenguin-archive-keyring_1.2_all.deb
sudo apt update
sudo apt install sm-elite
```

The `.deb` is also attached to each [GitHub release](https://github.com/hardenedpenguin/sm-elite/releases) for `sudo apt install ./sm-elite_<version>_all.deb`.

Then open `http://<node>/sm-elite/`. On an ASL3 appliance, port 80 forwards to HTTPS, so you land on `https://<node>/sm-elite/`. The first visit asks you to create the admin account. That needs the one-time setup code the install prints, which is also in `/var/lib/sm-elite/setup-code` (`sudo cat` it), so nobody else who reaches the page first can claim the node.

The package:

1. pulls in `python3-aiohttp`, `apache2`, and `sudo`
2. creates the `smelite` system account the dashboard runs as
3. reads the node numbers and callsign from `/etc/asterisk/rpt.conf` and the AMI login from `/etc/asterisk/manager.conf`, and writes `/etc/sm-elite/smelite.ini` and `nodes.ini` (readable only by root and the `smelite` group; the AMI password is never printed)
4. copies an existing Supermon `favorites.ini` into `/etc/sm-elite` when it finds one, so removing Supermon later leaves your favorites in place
5. starts the `sm-elite` service on `127.0.0.1:8420` and publishes it through Apache at `/sm-elite/` on ports 80 and 443
6. turns on the admin tools through `/usr/lib/sm-elite/smelite-admin` and its sudoers rule (set `[admin] enabled = no` to turn them off; see [Admin tools](admin.md))
7. writes `/etc/sm-elite/node_info.ini` (copied from supermon-ng when it is there) and starts `sm-elite-node-status.timer`, which fills in the [node status block](node-status.md)

**Updating**: `sudo apt update && sudo apt upgrade`, or install the newer release file the same way. Your config, users, and history are kept and the service restarts on the new version.

**Removing**: `sudo apt remove sm-elite` keeps `/etc/sm-elite` and `/var/lib/sm-elite`; `sudo apt purge sm-elite` deletes them too.

| Path | What |
| --- | --- |
| `/etc/sm-elite/` | `smelite.ini`, `nodes.ini`, `dvswitch.ini`, favorites, command templates |
| `/var/lib/sm-elite/` | users, sessions, last heard, node directory cache |
| `/usr/share/sm-elite/` | the program and the example config files |
| `/etc/apache2/conf-available/sm-elite.conf` | the `/sm-elite/` proxy |

Logs: `journalctl -u sm-elite`.

## HTTPS

`deploy/apache-sm-elite.conf` proxies `/sm-elite/` to the dashboard on `127.0.0.1`, including the websocket. It works inside the existing ASL3 Apache site, so it shares that site's certificate. The stock certificate is self-signed, so browsers warn once. With a real hostname, `certbot --apache` replaces it.

Over HTTPS the session cookie is `__Secure-smelite` (Secure, HttpOnly). Desktop notifications for keyup alerts need HTTPS.

The dashboard trusts `X-Forwarded-For` and `X-Forwarded-Proto` only from the addresses in `[server] trusted_proxies` (default `127.0.0.1, ::1`).
