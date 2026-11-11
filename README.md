# SM-Elite

[![Release](https://img.shields.io/github/v/release/hardenedpenguin/sm-elite?style=flat-square&label=release)](https://github.com/hardenedpenguin/sm-elite/releases)
[![Downloads](https://img.shields.io/github/downloads/hardenedpenguin/sm-elite/total?style=flat-square)](https://github.com/hardenedpenguin/sm-elite/releases)
[![Build](https://img.shields.io/github/actions/workflow/status/hardenedpenguin/sm-elite/release.yml?style=flat-square&label=build)](https://github.com/hardenedpenguin/sm-elite/actions/workflows/release.yml)
[![Last commit](https://img.shields.io/github/last-commit/hardenedpenguin/sm-elite?style=flat-square)](https://github.com/hardenedpenguin/sm-elite/commits/main)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)
[![Install: apt](https://img.shields.io/badge/install-apt-A81D33?style=flat-square&logo=debian&logoColor=white)](https://hardenedpenguin.github.io/hardenedpenguin-apt/)

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![aiohttp](https://img.shields.io/badge/aiohttp-asyncio-2C5BB4?style=flat-square&logo=aiohttp&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Debian%20%2F%20ASL3-A81D33?style=flat-square&logo=debian&logoColor=white)

> **Install and upgrades:** SM-Elite installs and updates through the **Debian `.deb` package**. Use the [hardenedpenguin APT repository](https://hardenedpenguin.github.io/hardenedpenguin-apt/) (`sudo apt install sm-elite`) or download a `.deb` from [Releases](https://github.com/hardenedpenguin/sm-elite/releases). See [Install on a node](#install-on-a-node).

One AllStar dashboard that keeps the part of each existing monitor that is actually good at its job.

| From | What SM-Elite keeps |
| --- | --- |
| Allmon3 | One process owns AMI and pushes full JSON snapshots. Monitoring is open, control requires a login. Transmit state is local, network, telemetry, playback, or idle. Voters are data, not HTML. |
| AllScan | Favorites use the Supermon `label[]` / `cmd[]` file. Activity (transmit percent, Rx%, link count) comes from the AllStar stats service, paced once on the server, without connecting to those nodes. |
| supermon-ng | Connect, monitor, local monitor, disconnect, permanent links, DTMF, and command templates on one bar. The node status block (CPU, weather, SkywarnPlus alerts, disk) is shown as the node reports it. One websocket carries every node. |

Everything is original code, apart from the node status updater, which comes from supermon-ng (see [Node status block](docs/node-status.md)). The pieces that age badly stay out: per-button PHP permission files, HTML built inside the poller, a websocket port per node, one AMI session per browser, and shell commands as the control path.

<p align="center">
  <a href="images/sm-elite-main.png" title="Click to enlarge">
    <img src="images/sm-elite-main.png" alt="SM-Elite dashboard with the control bar, live node cards, last heard, and favorites" width="640">
  </a>
  <br>
  <sub>Click the screenshot to see it full size.</sub>
</p>

## Features

- **Live node cards** with every connected link, direction, mode, and transmit state, plus the Supermon-style [status block](docs/node-status.md).
- **Control bar**: connect, monitor, local monitor, disconnect, disconnect all, DTMF, and command templates from `commands.ini`. It shows only for operators and admins, and only lists the nodes their account may control.
- **Last heard**: who keyed up, on which node, and for how long, kept for 30 days. The panel shows the latest 5 by default.
- **Display**: each browser picks which panels, node-card lines, and connection columns it shows, plus how many last heard rows. Handy for a kiosk screen that only needs the node cards.
- **[Themes](docs/themes.md)**: dark, light, blue, green, seafoam, and neon purple, plus custom themes you build, share as a file, or set as the site default.
- **Find a node**: search the AllStar node directory by callsign, city, hub name, or number, with your own [private node names](docs/node-names.md) mixed in.
- **Favorites** from a Supermon `favorites.ini`, with live activity for each favorite.
- **Link map**: a diagram of each node's connections, folded into its card.
- **Who can connect**: edit the node's allow and deny lists, and drop a denied node that is linked right now.
- **[Announce](docs/announce.md)**: type text and play it with Piper text-to-speech, on this node only or on every connected link. Save announcements and replay them later.
- **[DVSwitch](docs/dvswitch.md)**: switch a bridged node between DMR, YSF, P25, NXDN, D-STAR, and the other DVSwitch modes, pick a talkgroup from your list or type one, and restart the bridge services.
- **Keyup alerts**: a chime, an on-page notice, and (over HTTPS) a desktop notification when someone keys up on the nodes you pick.
- **System**: OS, CPU, memory, disks, temperature, addresses, service status, AllStar packages, and the Asterisk database.
- **[Schedule](docs/schedule.md)**: connect, monitor, or disconnect a link, play a saved announcement, or run a command from `commands.ini` at a set time on chosen days of the week, or once on a date. Handy for linking to a net and dropping it afterwards.
- **[Admin](docs/admin.md)**: reload or restart Asterisk, reboot or power off the server, edit `rpt.conf`, `iax.conf`, `extensions.conf`, and SM-Elite's own `asdb.txt` and `node_info.ini` with automatic backups, compare and restore backups, read the Asterisk log, and download or restore a [settings backup](docs/backup.md).
- **[Action log](docs/action-log.md)**: admins see who connected, disconnected, announced, scheduled, edited a file, or logged in, when, and from where.
- **[Users](docs/users.md)**: viewers watch, operators control nodes (optionally limited to certain node numbers), and admins also manage users and the admin tools. Everyone can change their own password under **Account**.

## Documentation

| Page | Covers |
| --- | --- |
| [Install on a node](docs/install.md) | the Debian package, what it sets up, updating, removing, file locations, HTTPS |
| [Configuration](docs/configuration.md) | every section of `smelite.ini` |
| [Users and accounts](docs/users.md) | roles, node limits, passwords |
| [Private node names](docs/node-names.md) | naming private, IRLP, and EchoLink nodes in `asdb.txt` |
| [Node status block](docs/node-status.md) | weather, alerts, and system lines under each node card |
| [Announce](docs/announce.md) | text-to-speech and saved sounds |
| [DVSwitch](docs/dvswitch.md) | bridges, networks, talkgroups, and the supermon-ng import |
| [Schedule](docs/schedule.md) | timed and one-time actions |
| [Themes](docs/themes.md) | built-in and custom themes, the site default |
| [Action log](docs/action-log.md) | what is recorded and for how long |
| [Admin tools](docs/admin.md) | the Admin page and how SM-Elite gets root access |
| [Settings backup](docs/backup.md) | downloading and restoring config, users, and history |
| [Development](docs/development.md) | demo mode, tests, building the package, releasing, code layout |

## Install on a node

On an AllStarLink 3 node (Debian 12 or 13), add the [hardenedpenguin apt repository](https://hardenedpenguin.github.io/hardenedpenguin-apt/) once, then install:

```bash
cd /tmp
curl -fsSLO https://hardenedpenguin.github.io/hardenedpenguin-apt/pool/main/h/hardenedpenguin-archive-keyring/hardenedpenguin-archive-keyring_1.2_all.deb
sudo apt install ./hardenedpenguin-archive-keyring_1.2_all.deb
sudo apt update
sudo apt install sm-elite
```

Then open `http://<node>/sm-elite/` and create the admin account with the one-time setup code the install prints. [Install on a node](docs/install.md) covers what the package sets up, HTTPS, updating, and removing.

## Thanks

SM-Elite exists because these projects showed what a good AllStar dashboard looks like. Thank you to their developers:

- **[Allmon3](https://github.com/AllStarLink/Allmon3)** by Jason McCormick, N8EI, and the AllStarLink team, for the single-poller design, JSON snapshots, and the clear split between watching and controlling a node.
- **[AllScan](https://github.com/davidgsd/AllScan)** by David Gleason, NR9V, for favorites with live activity from the AllStar stats service.
- **Supermon** by Tim Sawyer, WD6AWP, and later Paul Aidukas, KN2R, for the control bar, the `favorites.ini` format, and the node status block that every AllStar operator knows.
- **[supermon-ng](https://github.com/hardenedpenguin/supermon-ng)** for carrying Supermon forward, and for the DVSwitch mode and talkgroup layout SM-Elite imports.
- **[DVSwitch](https://dvswitch.org/)** by Mike Zingman, N4IRR, and Steve Zingman, N4IRS, for Analog_Bridge and MMDVM_Bridge.
- **[Piper](https://github.com/rhasspy/piper)** by Michael Hansen and the Rhasspy project, for the text-to-speech voices behind Announce.
- **[AllStarLink](https://www.allstarlink.org/)** and its volunteers, for the network and the ASL3 platform this runs on.
