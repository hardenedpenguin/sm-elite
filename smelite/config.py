"""Load SM-Elite configuration.

Node stanzas accept both Allmon3 (``pass``) and supermon-ng (``passwd``).
A stanza with ``nodes=`` and no host is a menu group. ``multinodes`` lists
extra node numbers that share the stanza's AMI login.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NodeConfig:
    node: str
    host: str
    port: int
    user: str
    password: str
    poll_interval: float = 1.0
    menu: bool = True
    system: str = "Nodes"
    voters: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    label: str = ""

    def ami(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
        }


@dataclass
class GroupConfig:
    name: str
    nodes: list[str]
    system: str = "Groups"


@dataclass
class CommandTemplate:
    command: str
    label: str


@dataclass
class Settings:
    listen: list[str] = field(default_factory=lambda: ["127.0.0.1"])
    port: int = 8420
    trusted_proxies: set[str] = field(default_factory=lambda: {"127.0.0.1", "::1"})
    public: str = "read"
    data_dir: Path = Path("data")
    demo: bool = False
    title: str = "SM-Elite"
    callsign: str = ""
    location: str = ""
    welcome: str = ""
    nodes_file: Path = Path("config/nodes.ini")
    favorites_dir: Path = Path("config")
    commands_file: Path = Path("config/commands.ini")
    astdb_url: str = "https://allmondb.allstarlink.org/"
    astdb_file: Path = Path("data/astdb.txt")
    private_db: Path = Path("config/asdb.txt")
    overrides: dict[str, str] = field(default_factory=dict)
    db_refresh_minutes: int = 15
    stats_enabled: bool = True
    stats_gap: float = 2.2
    history_enabled: bool = True
    history_days: int = 30
    admin_enabled: bool = False
    admin_config_dir: Path = Path("/etc/asterisk")
    admin_log_file: Path = Path("/var/log/asterisk/messages.log")
    admin_helper: str = "/usr/local/sbin/smelite-admin"
    sounds_dir: Path = Path("/usr/local/share/asterisk/sounds")
    voices_dir: Path = Path("/var/lib/piper-tts")
    piper: str = "/usr/bin/piper"
    sox: str = "/usr/bin/sox"
    tts_max_chars: int = 300
    system_services: list[str] = field(default_factory=lambda: ["asterisk"])
    dvswitch_file: Path = Path("config/dvswitch.ini")
    nodes: list[NodeConfig] = field(default_factory=list)
    groups: list[GroupConfig] = field(default_factory=list)
    commands: list[CommandTemplate] = field(default_factory=list)

    def node_by_id(self, node_id: str) -> NodeConfig | None:
        for node in self.nodes:
            if node.node == node_id or node_id in node.extras:
                return node
        return None

    def monitored_ids(self) -> list[str]:
        found: list[str] = []
        for node in self.nodes:
            found.append(node.node)
            found.extend(node.extras)
        return found


def _split_nodes(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _load_nodes(path: Path) -> tuple[list[NodeConfig], list[GroupConfig]]:
    if not path.is_file():
        return [], []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path, encoding="utf-8")
    nodes: list[NodeConfig] = []
    groups: list[GroupConfig] = []
    for section in parser.sections():
        entry = parser[section]
        host = entry.get("host", "").strip()
        listed = _split_nodes(entry.get("nodes", ""))
        if not host and listed:
            groups.append(GroupConfig(
                name=section,
                nodes=listed,
                system=entry.get("system", "Groups"),
            ))
            continue
        if not host or not section.strip():
            continue
        password = entry.get("pass", "") or entry.get("passwd", "")
        nodes.append(NodeConfig(
            node=section.strip(),
            host=host,
            port=entry.getint("port", 5038),
            user=entry.get("user", "admin").strip() or "admin",
            password=password,
            poll_interval=entry.getfloat("pollinterval", 1.0),
            menu=entry.get("menu", "yes").strip().lower() not in {"no", "false", "0"},
            system=entry.get("system", "Nodes").strip() or "Nodes",
            voters=_split_nodes(entry.get("voters", "")),
            extras=_split_nodes(entry.get("multinodes", "")),
            label=entry.get("label", "").strip(),
        ))
    return nodes, groups


def _load_commands(path: Path) -> list[CommandTemplate]:
    if not path.is_file():
        return _default_commands()
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    commands: list[CommandTemplate] = []
    for section in parser.sections():
        if not section.lower().startswith("syscmds"):
            continue
        for command, label in parser.items(section):
            commands.append(CommandTemplate(command=command.strip(), label=label.strip() or command.strip()))
    return commands or _default_commands()


def _default_commands() -> list[CommandTemplate]:
    return [
        CommandTemplate("rpt cmd @ status 12", "Say time"),
        CommandTemplate("rpt cmd @ status 11", "Force ID"),
        CommandTemplate("rpt stats @", "Node stats"),
        CommandTemplate("rpt lstats @", "Link stats"),
        CommandTemplate("core show uptime", "Server uptime"),
    ]


def load_settings(path: Path) -> Settings:
    settings = Settings()
    if not path.is_file():
        raise FileNotFoundError(path)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    root = path.parent

    def resolve(value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
        return (root / candidate).resolve()

    server = parser["server"] if parser.has_section("server") else {}
    settings.listen = _split_nodes(server.get("listen", "")) or settings.listen
    settings.port = int(server.get("port", settings.port))
    if "trusted_proxies" in server:
        settings.trusted_proxies = set(_split_nodes(server["trusted_proxies"]))
    settings.public = server.get("public", settings.public).strip().lower()
    if settings.public not in {"read", "none"}:
        settings.public = "read"
    settings.demo = server.get("demo", "no").strip().lower() in {"yes", "true", "1"}
    settings.data_dir = resolve(server.get("data_dir", "../data"))

    site = parser["site"] if parser.has_section("site") else {}
    settings.title = site.get("title", settings.title).strip() or settings.title
    settings.callsign = site.get("callsign", "").strip()
    settings.location = site.get("location", "").strip()
    settings.welcome = site.get("welcome", "").strip()

    nodes = parser["nodes"] if parser.has_section("nodes") else {}
    settings.nodes_file = resolve(nodes.get("file", "nodes.ini"))
    favorites = parser["favorites"] if parser.has_section("favorites") else {}
    settings.favorites_dir = resolve(favorites.get("directory", "."))
    commands = parser["commands"] if parser.has_section("commands") else {}
    settings.commands_file = resolve(commands.get("file", "commands.ini"))

    database = parser["database"] if parser.has_section("database") else {}
    settings.astdb_url = database.get("url", settings.astdb_url).strip()
    settings.astdb_file = resolve(database.get("file", "../data/astdb.txt"))
    settings.private_db = resolve(database.get("private", "asdb.txt"))
    settings.db_refresh_minutes = int(database.get("refresh_minutes", settings.db_refresh_minutes))
    if parser.has_section("node-overrides"):
        settings.overrides = {key: value.strip() for key, value in parser.items("node-overrides")}

    stats = parser["stats"] if parser.has_section("stats") else {}
    settings.stats_enabled = stats.get("enabled", "yes").strip().lower() not in {"no", "false", "0"}
    settings.stats_gap = float(stats.get("min_gap_seconds", settings.stats_gap))

    history = parser["history"] if parser.has_section("history") else {}
    settings.history_enabled = history.get("enabled", "yes").strip().lower() not in {"no", "false", "0"}
    settings.history_days = max(1, int(history.get("keep_days", settings.history_days)))

    admin = parser["admin"] if parser.has_section("admin") else {}
    settings.admin_enabled = admin.get("enabled", "no").strip().lower() in {"yes", "true", "1"}
    settings.admin_config_dir = Path(admin.get("config_dir", str(settings.admin_config_dir)))
    settings.admin_log_file = Path(admin.get("log_file", str(settings.admin_log_file)))
    settings.admin_helper = admin.get("helper", settings.admin_helper).strip()

    sounds = parser["sounds"] if parser.has_section("sounds") else {}
    settings.sounds_dir = Path(sounds.get("directory", str(settings.sounds_dir)))
    settings.voices_dir = Path(sounds.get("voices", str(settings.voices_dir)))
    settings.piper = sounds.get("piper", settings.piper).strip()
    settings.sox = sounds.get("sox", settings.sox).strip()
    settings.tts_max_chars = max(20, min(int(sounds.get("max_chars", settings.tts_max_chars)), 1000))

    system = parser["system"] if parser.has_section("system") else {}
    if system.get("services"):
        names = _split_nodes(system["services"])
        settings.system_services = [name for name in names if re.fullmatch(r"[A-Za-z0-9@._:-]{1,80}", name)][:20]

    dvswitch = parser["dvswitch"] if parser.has_section("dvswitch") else {}
    settings.dvswitch_file = resolve(dvswitch.get("file", "dvswitch.ini"))

    settings.nodes, settings.groups = _load_nodes(settings.nodes_file)
    settings.commands = _load_commands(settings.commands_file)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
