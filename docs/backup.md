# Settings backup

Admins find **Backup** on the Admin page. **Download backup** saves one `.tar.gz` holding:

- SM-Elite's config folder: `smelite.ini`, `nodes.ini`, `commands.ini`, `dvswitch.ini`, `node_info.ini`, `asdb.txt`, and the `favorites*.ini` files
- the users, scheduled actions, and site theme (`smelite.db`, `schedule.db`), with login sessions left out
- the last heard list and the action log (`history.db`, `actions.db`)

The file holds the AMI and hotspot passwords and the login password hashes, so keep it somewhere private.

To restore, choose a backup file. The page shows where and when it was made and what it holds, then you tick the parts to bring back:

- **Config files** are written through the [root helper](admin.md#root-access), each replaced file first copied to `/var/backups/smelite`, and SM-Elite restarts a few seconds later to load them. Check `nodes.ini` and `smelite.ini` when moving to a different node.
- **Users and scheduled actions** replace every account and schedule, and log everyone out, you included. Log back in with an account from the backup. A backup whose accounts include no admin is refused.
- **Last heard and the action log** replace those lists.

Restoring is recorded in the [action log](action-log.md).
