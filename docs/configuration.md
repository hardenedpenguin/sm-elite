# Configuration

`smelite.ini`, with paths relative to the file's own folder:

| Section | Keys |
| --- | --- |
| `[server]` | `listen` (comma-separated), `port`, `public` (`read` lets anyone watch, `none` requires a login), `data_dir`, `demo`, `trusted_proxies` |
| `[site]` | `title`, `callsign`, `location`, `welcome` |
| `[nodes]` | `file`: AMI logins in Allmon3 / supermon-ng stanza format (`host`, `port`, `user`, `pass` or `passwd`, `voters`, `multinodes`) |
| `[favorites]` | `directory` holding Supermon-style `favorites.ini` files |
| `[commands]` | `file` with the command templates for the More commands menu |
| `[database]` | `url`, `file`, `private` (your own node names, see [Private node names](node-names.md)), `refresh_minutes` for the AllStar node directory |
| `[stats]` | `enabled`, `min_gap_seconds` between AllStar stats requests |
| `[history]` | `enabled`, `keep_days` for last heard, `action_days` for the [action log](action-log.md) (default 90) |
| `[admin]` | `enabled`, `config_dir` (`/etc/asterisk`), `log_file`, `helper` |
| `[sounds]` | `directory`, `voices` (Piper `.onnx` models), `piper`, `sox`, `max_chars` (see [Announce](announce.md)) |
| `[system]` | `services` to show on the System page |
| `[dvswitch]` | `file` with the DVSwitch bridges and talkgroups (default `dvswitch.ini`, see [DVSwitch](dvswitch.md)) |
