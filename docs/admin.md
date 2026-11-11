# Admin tools

Admins open **Admin** in the header to reload or restart Asterisk, reboot or power off the server, edit `rpt.conf`, `iax.conf`, `extensions.conf`, and SM-Elite's own [`asdb.txt`](node-names.md) and [`node_info.ini`](node-status.md) with automatic backups, compare and restore backups, read the Asterisk log, and download or restore a [settings backup](backup.md).

Every change made from the Admin page asks for the admin's password again. Set `[admin] enabled = no` in `smelite.ini` to turn the Admin page off.

## Root access

The dashboard runs as an ordinary account. Anything that needs root goes through one small script, `/usr/lib/sm-elite/smelite-admin`, which that account may run with `sudo` and nothing else. The helper:

- writes only `rpt.conf`, `iax.conf`, `extensions.conf`, `/etc/sm-elite/asdb.txt`, and `/etc/sm-elite/node_info.ini`, and only when the file still matches the version the editor loaded
- writes SM-Elite's own config files in `/etc/sm-elite` when restoring a settings backup, keeping each file's owner and mode
- keeps the last 20 versions of each file in `/var/backups/smelite` (mode 600)
- saves and deletes announcements in the Asterisk sounds folder
- restarts the DVSwitch bridge services and SM-Elite itself, and starts the node status updater, and no other services
- schedules reboot and power off five seconds out, so the page can answer first
