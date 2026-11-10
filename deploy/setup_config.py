#!/usr/bin/python3
"""Write SM-Elite's config for this node. Called by the package postinst as root.

Reads the node numbers from rpt.conf and the AMI login from manager.conf, so
a fresh install works without typing either. Files that already exist are
left alone unless --force is given.
"""

from __future__ import annotations

import argparse
import configparser
import os
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path

NODE_SECTION = re.compile(r"^\[(\d{3,7})\]")
SERVICE_CANDIDATES = (
    "asterisk", "sm-elite", "apache2", "supermon-ng-backend", "allmon3", "skywarnplus-ng", "cron", "ssh",
)
SUPERMON_DVSWITCH = Path("/etc/supermon-ng/user_files/dvswitch_config.yml")
SUPERMON_FAVORITES = (Path("/etc/supermon-ng/user_files"), Path("/var/www/html/supermon"), Path("/var/www/html/supermon-ng"))
SUPERMON_NODE_INFO = Path("/etc/supermon-ng/user_files/node_info.ini")
SKYWARN_INSTALLED = Path("/var/lib/dpkg/info/skywarnplus-ng.list")
DVSWITCH_IMPORTED = "; Written by SM-Elite setup from supermon-ng"
DVSWITCH_MODES = ("DMR", "YSF", "P25", "NXDN", "DSTAR", "ASL", "STFU")
DEFAULT_DVSWITCH_INI = Path("/opt/MMDVM_Bridge/DVSwitch.ini")
USRP_CHANNEL = re.compile(r"^rxchannel\s*=\s*USRP/[\w.\-]+:(\d+):\d+", re.IGNORECASE)


def detect_nodes(rpt_conf: str) -> list[str]:
    nodes = []
    for line in rpt_conf.splitlines():
        match = NODE_SECTION.match(line.strip())
        if match and match[1] not in nodes:
            nodes.append(match[1])
    return nodes


def detect_callsign(rpt_conf: str) -> str:
    """The callsign from a node's ``idrecording = |iCALL`` (Morse ID) line."""
    found: dict[str, str] = {}
    section = ""
    for line in rpt_conf.splitlines():
        line = re.sub(r";.*$", "", line).strip()
        header = re.match(r"^\[([^\]]+)\]", line)
        if header:
            section = header[1]
            continue
        match = re.match(r"^(idrecording|idtalkover)\s*=\s*\|i([A-Za-z0-9/]{3,10})$", line)
        if match and match[2].upper() != "NOTSET":
            found.setdefault(section, match[2].upper())
    for node in detect_nodes(rpt_conf):
        if node in found:
            return found[node]
    return next(iter(found.values()), "")


def detect_ami(manager_conf: str) -> dict | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False, comment_prefixes=(";", "#"))
    parser.read_string(_asterisk_to_ini(manager_conf))
    port = 5038
    if parser.has_section("general"):
        if parser["general"].get("enabled", "yes").strip().lower() not in {"yes", "true", "1", "on"}:
            return None
        port = int(parser["general"].get("port", "5038") or 5038)
    users = [name for name in parser.sections() if name != "general" and parser[name].get("secret")]
    if not users:
        return None
    user = "admin" if "admin" in users else users[0]
    return {"port": port, "user": user, "secret": parser[user]["secret"].strip()}


def _asterisk_to_ini(text: str) -> str:
    """Asterisk syntax configparser rejects: ';' comments anywhere (\\; is literal),
    #include lines, and [name](template) headers."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"(?<!\\);.*$", "", line).replace("\\;", ";").rstrip()
        if line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r"^(\[[^\]]+\])\(.*\)$", r"\1", line))
    return "\n".join(lines)


def nodes_ini(nodes: list[str], ami: dict) -> str:
    out = ["; Written by SM-Elite setup from rpt.conf and manager.conf. Keep this file private.", ""]
    for node in nodes:
        out += [
            f"[{node}]",
            "host = 127.0.0.1",
            f"port = {ami['port']}",
            f"user = {ami['user']}",
            f"pass = {ami['secret']}",
            "",
        ]
    return "\n".join(out)


def node_info_ini(nodes: list[str], alerts: bool) -> str:
    return f"""; Node status block settings. sm-elite-node-status.timer reads this every few
; minutes and publishes uptime, load, CPU temperature, weather, disk, and
; weather alerts for each NODE.
;
; Weather needs saytime-weather-rb (/usr/sbin/weather.rb): set WX_CODE to a ZIP
; or airport code and WX_LOCATION to the name to show, or set WX_USE_GPS = yes.
; Alerts come from SkywarnPlus-NG or CANWarn-NG on this node.

[general]
NODE = {" ".join(nodes)}
WX_USE_GPS = no
WX_CODE = 00000
WX_LOCATION = City, State
TEMP_UNIT = F
ALERT_PROVIDER = skywarnplus

[skywarnplus]
MASTER_ENABLE = {"yes" if alerts else "no"}
API_URL = http://127.0.0.1:8100

[canwarn_ng]
MASTER_ENABLE = no
API_URL = http://127.0.0.1:8110
"""


def seed_node_info(conf: Path, nodes: list[str], source: Path = SUPERMON_NODE_INFO,
                   skywarn_marker: Path = SKYWARN_INSTALLED) -> str | None:
    """Write node_info.ini once: Supermon's copy when there is one, else built from rpt.conf."""
    target = conf / "node_info.ini"
    if target.exists():
        return None
    if source.is_file():
        shutil.copy(source, target)
        found = f"copied from {source}"
    elif nodes:
        target.write_text(node_info_ini(nodes, skywarn_marker.exists()), encoding="utf-8")
        found = f"written for {', '.join(nodes)}"
    else:
        return None
    os.chmod(target, 0o644)
    return found


def smelite_ini(*, listen: list[str], port: int, data_dir: Path, favorites_dir: Path, callsign: str,
                location: str, admin: bool, services: list[str],
                helper: str = "/usr/lib/sm-elite/smelite-admin") -> str:
    return f"""; Written by SM-Elite setup. Edit freely; upgrades will not overwrite it.
; Restart after editing: sudo systemctl restart sm-elite
[server]
listen = {", ".join(listen)}
port = {port}
; read = anyone can watch, none = login required to see anything
public = read
data_dir = {data_dir}
demo = no
; Requests from these addresses may set X-Forwarded-For / X-Forwarded-Proto (the Apache proxy).
trusted_proxies = 127.0.0.1, ::1

[site]
title = SM-Elite
callsign = {callsign}
location = {location}
welcome =

[nodes]
file = nodes.ini

[favorites]
directory = {favorites_dir}

[commands]
file = commands.ini

[database]
url = https://allmondb.allstarlink.org/
file = {data_dir / "astdb.txt"}
private = asdb.txt
refresh_minutes = 15

[stats]
enabled = yes
min_gap_seconds = 2.2

[history]
enabled = yes
keep_days = 30

[admin]
; Reload/restart Asterisk, reboot, edit rpt/iax/extensions.conf.
; Saving files and power actions go through this root helper and its sudoers rule.
enabled = {"yes" if admin else "no"}
helper = {helper}

[system]
services = {", ".join(services)}

[node-overrides]
"""


def active_services() -> list[str]:
    try:
        out = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ["asterisk", "sm-elite"]
    running = {line.split()[0].removesuffix(".service") for line in out.splitlines() if line.strip()}
    found = [name for name in SERVICE_CANDIDATES if name in running or name == "sm-elite"]
    found += sorted(name for name in running if name.startswith("wg-quick@"))
    return found


def supermon_favorites(candidates: tuple[Path, ...] = SUPERMON_FAVORITES) -> Path | None:
    for candidate in candidates:
        if (candidate / "favorites.ini").is_file():
            return candidate
    return None


def seed_favorites(conf: Path, source: Path | None, examples: Path) -> list[Path]:
    """Copy favorites into SM-Elite's own folder so removing Supermon cannot take them away."""
    copied = []
    if source is not None:
        for path in sorted(source.glob("favorites*.ini")):
            target = conf / path.name
            if path.is_file() and not target.exists():
                shutil.copy(path, target)
                copied.append(target)
    target = conf / "favorites.ini"
    if not target.exists():
        shutil.copy(examples / "favorites.ini", target)
        copied.append(target)
    return copied


def missing_favorites_dir(ini_text: str, conf: Path) -> Path | None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(ini_text)
    raw = parser.get("favorites", "directory", fallback="").strip()
    if not raw:
        return None
    path = Path(raw) if Path(raw).is_absolute() else conf / raw
    return None if path.is_dir() else path


def repoint_favorites(ini_text: str, conf: Path) -> str:
    lines, section = [], ""
    for line in ini_text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            section = stripped.strip("[]").strip().lower()
        elif section == "favorites" and re.match(r"directory\s*=", stripped):
            line = f"directory = {conf}\n"
        lines.append(line)
    return "".join(lines)


def analog_bridge_inis() -> list[Path]:
    return [Path("/opt/Analog_Bridge/Analog_Bridge.ini"), *sorted(Path("/etc/dvswitch").glob("*/Analog_Bridge.ini"))]


def _usrp_rx_port(path: Path) -> str:
    parser = configparser.RawConfigParser(inline_comment_prefixes=(";",), strict=False, allow_no_value=True)
    try:
        parser.read_string("[preamble]\n" + path.read_text(errors="replace"))
        return (parser["USRP"]["rxPort"] or "").strip()
    except (OSError, KeyError, configparser.Error):
        return ""


def detect_bridges(rpt_conf: str, analog_inis: list[Path]) -> list[dict]:
    """Nodes wired to Analog_Bridge by ``rxchannel = USRP/host:port:port``, with
    the bridge's /tmp/ABInfo_<port>.json and the DVSwitch.ini holding its mode ports."""
    by_port: dict[str, Path] = {}
    for path in analog_inis:
        port = _usrp_rx_port(path)
        if port:
            by_port.setdefault(port, path)
    found: list[dict] = []
    section = ""
    for line in rpt_conf.splitlines():
        line = re.sub(r";.*$", "", line).strip()
        if line.startswith("["):
            header = NODE_SECTION.match(line)
            section = header[1] if header else ""
            continue
        match = USRP_CHANNEL.match(line)
        if not section or not match or any(bridge["node"] == section for bridge in found):
            continue
        ini = by_port.get(match[1])
        dvswitch_ini = DEFAULT_DVSWITCH_INI
        if ini is not None and (ini.parent / "DVSwitch.ini").is_file():
            dvswitch_ini = ini.parent / "DVSwitch.ini"
        found.append({"node": section, "abinfo": f"/tmp/ABInfo_{match[1]}.json", "dvswitch_ini": str(dvswitch_ini)})
    return found


def _ini_name(name: str) -> str:
    return re.sub(r"[^\w.\-]", "-", name.strip().lower()) or "network"


def _ini_alias(name: str, taken: set[str]) -> str:
    alias = re.sub(r"\s+", " ", name.replace("=", "-")).strip() or "Talkgroup"
    if alias[0] in ";#[":
        alias = f"TG {alias}"
    candidate, count = alias, 2
    while candidate in taken:
        candidate, count = f"{alias} ({count})", count + 1
    taken.add(candidate)
    return candidate


def supermon_dvswitch(data: dict) -> tuple[dict[str, dict[str, str]], dict[str, list[tuple[str, str]]], list[str]]:
    """Networks, talkgroups per mode, and skipped mode names from supermon-ng's
    dvswitch_config.yml (``credentials`` profiles plus ``profile``/``tg`` or raw ``tgid`` rows)."""
    networks: dict[str, dict[str, str]] = {}
    for name, entry in (data.get("credentials") or {}).items():
        if isinstance(entry, dict):
            net = {
                "label": "",
                "password": str(entry.get("password") or ""),
                "server": str(entry.get("server") or entry.get("host") or ""),
                "port": str(entry.get("port") or "62031"),
            }
            if entry.get("format"):
                net["format"] = str(entry["format"])
            networks[_ini_name(str(name))] = net
    modes: dict[str, list[tuple[str, str]]] = {}
    skipped: list[str] = []
    for block in data.get("modes") or []:
        if not isinstance(block, dict) or not block:
            continue
        raw = next(iter(block))
        mode = str(raw).upper().replace("-", "")
        if mode not in DVSWITCH_MODES:
            skipped.append(str(raw))
            continue
        details = block[raw] if isinstance(block[raw], dict) else {}
        rows = modes.setdefault(mode, [])
        taken = {alias for alias, _ in rows}
        for row in details.get("talkgroups") or []:
            if not isinstance(row, dict):
                continue
            profile = row.get("profile")
            if isinstance(profile, str) and profile.strip():
                net = _ini_name(profile)
                tg = str(row.get("tg") if row.get("tg") is not None else row.get("tgid") or "").strip()
                if net not in networks or not tg:
                    continue
                if row.get("network") and not networks[net]["label"]:
                    networks[net]["label"] = str(row["network"])
                value, public = f"@{net} {tg}", tg
            elif row.get("tgid") is not None and str(row["tgid"]).strip():
                value = str(row["tgid"])
                public = value.rsplit("!", 1)[-1] if "@" in value else value.strip()
            else:
                continue
            rows.append((_ini_alias(str(row.get("alias") or public), taken), value))
    return networks, modes, skipped


def _quoted(value: str) -> str:
    return '"' + value.replace('"', "'").replace("\n", " ") + '"'


def dvswitch_ini(bridges: list[dict], networks: dict[str, dict[str, str]],
                 modes: dict[str, list[tuple[str, str]]], source: Path) -> str:
    out = [
        f"{DVSWITCH_IMPORTED} ({source})",
        "; and the USRP channels in rpt.conf. Edit freely; setup will not import again.",
        "; The format is explained in /usr/share/sm-elite/examples/dvswitch.ini.",
    ]
    for bridge in bridges:
        out += ["", f"[node {bridge['node']}]", f"abinfo = {bridge['abinfo']}", f"dvswitch_ini = {bridge['dvswitch_ini']}"]
    for name, net in networks.items():
        out += ["", f"[network {name}]", f"label = {_quoted(net['label'] or name)}"]
        out += [f"{key} = {_quoted(value)}" for key, value in net.items() if key != "label"]
    for mode, rows in modes.items():
        if rows:
            out += ["", f"[mode {mode}]"]
            out += [f"{alias} = {_quoted(value)}" for alias, value in rows]
    return "\n".join(out) + "\n"


def dvswitch_unused(path: Path) -> bool:
    """True when dvswitch.ini is missing, or no node uses it and it was not imported before."""
    if not path.exists():
        return True
    text = path.read_text(errors="replace")
    if text.startswith(DVSWITCH_IMPORTED):
        return False
    return not any(re.match(r"^\s*\[node\s+\d+\]", line) for line in text.splitlines())


def import_dvswitch(source: Path, rpt_conf: str, analog_inis: list[Path]) -> str | None:
    try:
        import yaml
    except ImportError:
        print(f"dvswitch.ini: install python3-yaml to import {source}", file=sys.stderr)
        return None
    try:
        data = yaml.safe_load(source.read_text(errors="replace")) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"dvswitch.ini: could not read {source}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        return None
    networks, modes, skipped = supermon_dvswitch(data)
    bridges = detect_bridges(rpt_conf, analog_inis)
    count = sum(len(rows) for rows in modes.values())
    print(f"dvswitch.ini: imported {count} talkgroups ({', '.join(m for m in modes if modes[m])}) from supermon-ng")
    for bridge in bridges:
        print(f"dvswitch.ini: node {bridge['node']} uses {bridge['abinfo']} and {bridge['dvswitch_ini']}")
    if not bridges:
        print("dvswitch.ini: no node has a USRP channel in rpt.conf; add a [node N] section to show DVSwitch")
    if skipped:
        print(f"dvswitch.ini: skipped modes SM-Elite does not know: {', '.join(skipped)}")
    extra = sorted(source.parent.glob("dvswitch_config_*.yml"))
    if extra:
        print(f"dvswitch.ini: per-node lists were not imported: {', '.join(path.name for path in extra)}")
    return dvswitch_ini(bridges, networks, modes, source)


def write_private(path: Path, text: str, owner: str, mode: int) -> None:
    path.write_text(text, encoding="utf-8")
    entry = pwd.getpwnam(owner)
    os.chown(path, 0, entry.pw_gid)
    os.chmod(path, mode)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--examples", required=True, type=Path, help="the repo's config/ folder")
    parser.add_argument("--user", required=True)
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument("--callsign", default="")
    parser.add_argument("--location", default="")
    parser.add_argument("--admin", action="store_true")
    parser.add_argument("--helper", default="/usr/lib/sm-elite/smelite-admin")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rpt-conf", type=Path, default=Path("/etc/asterisk/rpt.conf"))
    parser.add_argument("--manager-conf", type=Path, default=Path("/etc/asterisk/manager.conf"))
    parser.add_argument("--supermon-dvswitch", type=Path, default=SUPERMON_DVSWITCH)
    args = parser.parse_args(argv)

    conf, data = args.config_dir, args.data_dir
    conf.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    entry = pwd.getpwnam(args.user)
    os.chown(conf, 0, entry.pw_gid)
    os.chmod(conf, 0o750)
    os.chown(data, entry.pw_uid, entry.pw_gid)
    os.chmod(data, 0o750)

    for name in ("commands.ini", "asdb.txt"):
        if not (conf / name).exists():
            shutil.copy(args.examples / name, conf / name)
            os.chmod(conf / name, 0o644)
    dvs_path = conf / "dvswitch.ini"
    if args.supermon_dvswitch.is_file() and dvswitch_unused(dvs_path):
        rpt_text = args.rpt_conf.read_text(errors="replace") if args.rpt_conf.is_file() else ""
        imported = import_dvswitch(args.supermon_dvswitch, rpt_text, analog_bridge_inis())
        if imported is not None:
            if dvs_path.exists():
                shutil.copy2(dvs_path, conf / "dvswitch.ini.before-import")
            write_private(dvs_path, imported, args.user, 0o640)
    if not dvs_path.exists():
        write_private(dvs_path, (args.examples / "dvswitch.ini").read_text(encoding="utf-8"), args.user, 0o640)

    nodes_path = conf / "nodes.ini"
    if args.force or not nodes_path.exists():
        nodes = detect_nodes(args.rpt_conf.read_text(errors="replace")) if args.rpt_conf.is_file() else []
        ami = detect_ami(args.manager_conf.read_text(errors="replace")) if args.manager_conf.is_file() else None
        if not nodes or ami is None:
            print("Could not find node numbers in rpt.conf or an AMI login in manager.conf.", file=sys.stderr)
            print(f"Copying the example; edit {nodes_path} before starting.", file=sys.stderr)
            write_private(nodes_path, (args.examples / "nodes.ini").read_text(), args.user, 0o640)
        else:
            write_private(nodes_path, nodes_ini(nodes, ami), args.user, 0o640)
            print(f"nodes.ini: {', '.join(nodes)} via AMI user '{ami['user']}' on port {ami['port']}")
    else:
        print(f"nodes.ini: kept existing {nodes_path}")

    rpt_nodes = detect_nodes(args.rpt_conf.read_text(errors="replace")) if args.rpt_conf.is_file() else []
    status = seed_node_info(conf, rpt_nodes)
    if status is not None:
        print(f"node_info.ini: {status}")

    ini_path = conf / "smelite.ini"
    if args.force or not ini_path.exists():
        source = supermon_favorites()
        for path in seed_favorites(conf, source, args.examples):
            os.chown(path, entry.pw_uid, entry.pw_gid)
        text = smelite_ini(
            listen=[part.strip() for part in args.listen.split(",") if part.strip()],
            port=args.port,
            data_dir=data,
            favorites_dir=conf,
            callsign=args.callsign or (detect_callsign(args.rpt_conf.read_text(errors="replace")) if args.rpt_conf.is_file() else ""),
            location=args.location,
            admin=args.admin,
            services=active_services(),
            helper=args.helper,
        )
        write_private(ini_path, text, args.user, 0o640)
        print(f"smelite.ini: written (favorites copied from {source or 'the example'} into {conf})")
    else:
        text = ini_path.read_text(encoding="utf-8", errors="replace")
        gone = missing_favorites_dir(text, conf)
        if gone is not None:
            for path in seed_favorites(conf, supermon_favorites(), args.examples):
                os.chown(path, entry.pw_uid, entry.pw_gid)
            write_private(ini_path, repoint_favorites(text, conf), args.user, 0o640)
            print(f"smelite.ini: favorites folder {gone} no longer exists; now using {conf}")
        else:
            print(f"smelite.ini: kept existing {ini_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
