"""Build and check app_rpt commands.

Link codes match app_rpt function_ilink: 1/11 disconnect, 2/12 monitor,
3/13 transceive, 6 disconnect all, 8/18 local monitor. The permanent form
is the base code plus 10. Disconnect always uses 11 so a permanent link
actually drops; code 1 is ignored on a permanent link.
"""

from __future__ import annotations

import re

NODE_RE = re.compile(r"^[0-9]{1,8}$")
DTMF_RE = re.compile(r"^[0-9*#A-Da-d]+$")
ALLOWED_PREFIXES = ("rpt ", "voter ", "database ", "iax2 ", "core show ", "susb ", "radio ")
_FORBIDDEN = set(";|&\n\r`$")


class CommandError(ValueError):
    """The command is not safe to send."""


def _node(value: str, label: str) -> str:
    text = str(value).strip()
    if not NODE_RE.fullmatch(text):
        raise CommandError(f"{label} must be a node number")
    return text


def check_command(command: str) -> str:
    """Return the command when it is a single allowlisted manager command."""
    if any(ch in command for ch in _FORBIDDEN):
        raise CommandError("command contains a forbidden character")
    text = " ".join(command.strip().split())
    if not text or len(text) > 256:
        raise CommandError("command length is invalid")
    if any(ch in text for ch in _FORBIDDEN):
        raise CommandError("command contains a forbidden character")
    lowered = text.lower()
    if not lowered.startswith(ALLOWED_PREFIXES):
        raise CommandError("command is not in the allowlist")
    return text


def expand_template(template: str, local: str, remote: str | None) -> str:
    """Replace ``@`` with the local node and ``%`` with the selected remote."""
    local = _node(local, "local node")
    text = template.replace("@", local)
    if "%" in text:
        if not remote:
            raise CommandError("this command needs a selected node")
        text = text.replace("%", _node(remote, "target node"))
    return check_command(text)


def ilink(local: str, code: int, target: str | None = None) -> str:
    local = _node(local, "local node")
    if code not in {1, 2, 3, 6, 8, 11, 12, 13, 18}:
        raise CommandError("unknown link code")
    if code == 6:
        return check_command(f"rpt cmd {local} ilink 6")
    if not target:
        raise CommandError("target node is required")
    target = _node(target, "target node")
    return check_command(f"rpt cmd {local} ilink {code} {target}")


def dtmf(local: str, digits: str) -> str:
    local = _node(local, "local node")
    digits = digits.strip().upper()
    if not digits or not DTMF_RE.fullmatch(digits) or len(digits) > 32:
        raise CommandError("DTMF must be 0-9, *, #, or A-D")
    return check_command(f"rpt fun {local} {digits}")


def action_command(
    action: str,
    local: str,
    target: str | None = None,
    *,
    permanent: bool = False,
    digits: str | None = None,
    template: str | None = None,
) -> str:
    """Map a dashboard action onto one manager command."""
    name = action.strip().lower()
    extra = 10 if permanent else 0
    if name == "connect":
        return ilink(local, 3 + extra, target)
    if name == "monitor":
        return ilink(local, 2 + extra, target)
    if name == "local_monitor":
        return ilink(local, 8 + extra, target)
    if name == "disconnect":
        return ilink(local, 11, target)
    if name == "disconnect_all":
        return ilink(local, 6)
    if name == "dtmf":
        return dtmf(local, digits or "")
    if name == "custom":
        if not template:
            raise CommandError("missing command")
        return expand_template(template, local, target)
    raise CommandError("unknown action")
