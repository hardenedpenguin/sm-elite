# Node status block

The lines under each node card's title (weather, SkywarnPlus-NG alerts, CPU temperature, uptime, load, and disk) are app_rpt variables the node publishes. The package keeps them current with `sm-elite-node-status.timer`, which runs `/usr/lib/sm-elite/ast_node_status_update.py` one minute after boot and every 4 minutes after that. The updater comes from supermon-ng.

Its settings are in `/etc/sm-elite/node_info.ini`:

```ini
[general]
NODE = 546050 546051
WX_USE_GPS = no
WX_CODE = 77511
WX_LOCATION = Alvin, Texas
TEMP_UNIT = F
ALERT_PROVIDER = skywarnplus

[skywarnplus]
MASTER_ENABLE = yes
API_URL = http://127.0.0.1:8100
```

- `NODE` lists the nodes to update. A fresh install fills it in from `rpt.conf`.
- Weather needs [saytime_weather_rb](https://github.com/hardenedpenguin/saytime_weather_rb) (`sudo apt install saytime-weather-rb`). Set `WX_CODE` to a ZIP or airport code and `WX_LOCATION` to the name to show, or set `WX_USE_GPS = yes`. With the placeholder `00000` the weather line stays empty.
- Alerts come from [SkywarnPlus-NG](https://github.com/hardenedpenguin/SkywarnPlus-NG), or CANWarn-NG with `ALERT_PROVIDER = canwarn` and its `[canwarn_ng]` section. The install turns SkywarnPlus-NG alerts on when that package is installed.
- `TEMP_UNIT` is `F` or `C`.

Edit it on the Admin page (Config files, then `node_info.ini`); saving there runs the updater right away. Changes made from a shell apply on the next run, or run it now with `sudo systemctl start sm-elite-node-status`. Its output is in `journalctl -u sm-elite-node-status`.

While supermon-ng is installed with its own status timer on, SM-Elite's updater skips each run and leaves the job to supermon-ng. After supermon-ng is removed, SM-Elite's copy of its `node_info.ini` takes over.
