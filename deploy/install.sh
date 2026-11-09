#!/usr/bin/env bash
# Install or update SM-Elite on an AllStarLink 3 node. Run as root from the checkout:
#
#   sudo ./deploy/install.sh [options]
#
#   --user NAME        account the dashboard runs as (default: the sudo user)
#   --app-dir PATH     where the code lives (default: this checkout, used in place)
#   --config-dir PATH  config folder (default: /etc/sm-elite, or this checkout's
#                      config/ when it already holds a non-demo smelite.ini)
#   --data-dir PATH    users, sessions, history (default: /var/lib/sm-elite)
#   --port N           dashboard port (default: 8420)
#   --listen ADDRS     extra addresses to listen on besides 127.0.0.1, comma-separated
#   --callsign CALL    shown in the page header
#   --location TEXT    shown in the page header
#   --admin            install the root helper and sudoers rule, turn on admin tools
#   --no-https         skip the Apache /sm-elite/ proxy
#   --force-config     regenerate smelite.ini and nodes.ini (the old ones are backed up)
#
# Running it again updates the service, helper, and Apache snippet; existing
# config files are kept unless --force-config is given.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-}"
APP_DIR="$SRC"
CONFIG_DIR=""
DATA_DIR="/var/lib/sm-elite"
PORT=8420
LISTEN_EXTRA=""
CALLSIGN=""
LOCATION=""
ADMIN=0
HTTPS=1
FORCE=0

die() { echo "install.sh: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --user) RUN_USER="$2"; shift 2 ;;
        --app-dir) APP_DIR="$2"; shift 2 ;;
        --config-dir) CONFIG_DIR="$2"; shift 2 ;;
        --data-dir) DATA_DIR="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --listen) LISTEN_EXTRA="$2"; shift 2 ;;
        --callsign) CALLSIGN="$2"; shift 2 ;;
        --location) LOCATION="$2"; shift 2 ;;
        --admin) ADMIN=1; shift ;;
        --no-https) HTTPS=0; shift ;;
        --force-config) FORCE=1; shift ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) die "unknown option $1 (try --help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "run as root (sudo ./deploy/install.sh)"
if dpkg-query -W -f='${Status}' sm-elite 2>/dev/null | grep -q "install ok installed"; then
    die "the sm-elite package is installed; update it with apt instead of this script"
fi
[ -n "$RUN_USER" ] || die "pass --user NAME (the account the dashboard runs as)"
id "$RUN_USER" >/dev/null 2>&1 || die "user $RUN_USER does not exist"
[ "$RUN_USER" != root ] || die "do not run the dashboard as root; pass --user"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be a number"
RUN_GROUP="$(id -gn "$RUN_USER")"

step "Checking Python"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is required"
if ! python3 -c 'import aiohttp' 2>/dev/null; then
    apt-get install -y python3-aiohttp
fi
python3 -c 'import aiohttp; print("aiohttp", aiohttp.__version__)'

if [ "$APP_DIR" != "$SRC" ]; then
    step "Copying code to $APP_DIR"
    mkdir -p "$APP_DIR"
    for part in smelite web deploy config README.md; do
        rm -rf "${APP_DIR:?}/$part.new"
        cp -a "$SRC/$part" "$APP_DIR/$part.new"
        rm -rf "${APP_DIR:?}/$part"
        mv "$APP_DIR/$part.new" "$APP_DIR/$part"
    done
    chown -R root:root "$APP_DIR"
    chmod -R go-w "$APP_DIR"
fi

if [ -z "$CONFIG_DIR" ]; then
    if [ -f "$APP_DIR/config/smelite.ini" ] && grep -qiE '^\s*demo\s*=\s*(no|false|0)' "$APP_DIR/config/smelite.ini"; then
        CONFIG_DIR="$APP_DIR/config"
    else
        CONFIG_DIR="/etc/sm-elite"
    fi
fi

LISTEN="127.0.0.1"
[ -n "$LISTEN_EXTRA" ] && LISTEN="$LISTEN, $LISTEN_EXTRA"

step "Config in $CONFIG_DIR"
setup_args=(--config-dir "$CONFIG_DIR" --data-dir "$DATA_DIR" --examples "$SRC/config" --user "$RUN_USER"
            --listen "$LISTEN" --port "$PORT" --callsign "$CALLSIGN" --location "$LOCATION")
[ "$ADMIN" -eq 1 ] && setup_args+=(--admin)
if [ "$FORCE" -eq 1 ]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    for f in smelite.ini nodes.ini; do
        [ -f "$CONFIG_DIR/$f" ] && cp -p "$CONFIG_DIR/$f" "$CONFIG_DIR/$f.$stamp" && echo "backed up $f to $f.$stamp"
    done
    setup_args+=(--force)
fi
if [ "$CONFIG_DIR" = "$APP_DIR/config" ] && [ "$FORCE" -eq 0 ]; then
    echo "Using the existing config in $CONFIG_DIR (not regenerated)."
else
    python3 "$SRC/deploy/setup_config.py" "${setup_args[@]}"
fi

if [ "$HTTPS" -eq 1 ] && ! grep -qE '^\s*listen\s*=.*127\.0\.0\.1' "$CONFIG_DIR/smelite.ini"; then
    echo "Note: $CONFIG_DIR/smelite.ini does not listen on 127.0.0.1; adding it so Apache can reach the dashboard."
    sed -i -E 's/^(\s*listen\s*=\s*)(.*)$/\1127.0.0.1, \2/' "$CONFIG_DIR/smelite.ini"
fi

if [ "$ADMIN" -eq 0 ] && [ -f /usr/local/sbin/smelite-admin ]; then
    install -o root -g root -m 755 "$SRC/deploy/smelite-admin" /usr/local/sbin/smelite-admin
    echo "Refreshed the admin helper already installed in /usr/local/sbin"
fi

if [ "$ADMIN" -eq 1 ]; then
    step "Installing the admin helper and sudoers rule"
    install -o root -g root -m 755 "$SRC/deploy/smelite-admin" /usr/local/sbin/smelite-admin
    tmp="$(mktemp)"
    sed "s/^smelite /$RUN_USER /" "$SRC/deploy/sudoers-smelite" > "$tmp"
    visudo -cf "$tmp" >/dev/null || { rm -f "$tmp"; die "the sudoers rule did not validate"; }
    install -o root -g root -m 440 "$tmp" /etc/sudoers.d/smelite
    rm -f "$tmp"
    sudo -u "$RUN_USER" sudo -n /usr/local/sbin/smelite-admin check >/dev/null || die "the helper check failed"
    echo "helper ok: $RUN_USER may run only /usr/local/sbin/smelite-admin as root"
    if grep -qE '^\s*\[admin\]' "$CONFIG_DIR/smelite.ini"; then
        sed -i -E '/^\s*\[admin\]/,/^\s*\[/{s/^(\s*enabled\s*=\s*).*/\1yes/}' "$CONFIG_DIR/smelite.ini"
    else
        printf '\n[admin]\nenabled = yes\n' >> "$CONFIG_DIR/smelite.ini"
    fi
fi

step "Installing the systemd service"
sed -e "s|@USER@|$RUN_USER|" -e "s|@GROUP@|$RUN_GROUP|" -e "s|@APP_DIR@|$APP_DIR|" \
    -e "s|@CONFIG@|$CONFIG_DIR/smelite.ini|" "$SRC/deploy/sm-elite.service" > /etc/systemd/system/sm-elite.service
systemctl daemon-reload
systemctl enable sm-elite.service >/dev/null
systemctl restart sm-elite.service
for _ in $(seq 20); do
    curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" 2>/dev/null && break
    sleep 0.5
done
curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/health" || die "the dashboard did not answer on 127.0.0.1:$PORT (journalctl -u sm-elite)"
echo "sm-elite is running"

if [ "$HTTPS" -eq 1 ]; then
    if ! command -v apache2ctl >/dev/null; then
        echo "Apache is not installed; skipping HTTPS. Re-run after installing apache2, or use --no-https."
        HTTPS=0
    else
        step "Publishing https://<node>/sm-elite/ through Apache"
        sed "s|@PORT@|$PORT|g" "$SRC/deploy/apache-sm-elite.conf" > /etc/apache2/conf-available/sm-elite.conf
        a2enmod -q ssl proxy proxy_http headers alias >/dev/null
        a2enconf -q sm-elite >/dev/null
        if ! apache2ctl configtest 2>/dev/null; then
            a2disconf -q sm-elite >/dev/null
            apache2ctl configtest || true
            die "Apache rejected the config; sm-elite.conf was disabled again"
        fi
        systemctl reload apache2
        code="$(curl -ksS -o /dev/null -w '%{http_code}' https://127.0.0.1/sm-elite/api/health || true)"
        [ "$code" = 200 ] || die "https://127.0.0.1/sm-elite/ answered $code"
        echo "Apache proxy ok"
    fi
fi

step "Done"
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ "$HTTPS" -eq 1 ] && echo "Dashboard: https://${ip:-<node>}/sm-elite/"
listen_now="$(sed -nE 's/^\s*listen\s*=\s*//p' "$CONFIG_DIR/smelite.ini" | head -n1)"
echo "Direct:    port $PORT on ${listen_now:-127.0.0.1}"
echo "If no account exists yet, the first visit asks you to create the admin account."
