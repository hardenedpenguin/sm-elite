# Action log

Admins find it under **System**, then **Action log**. It lists each control action (connect, disconnect, DTMF, commands), announcement, allow and deny list change, DVSwitch change, favorite added or removed, scheduled action added, edited, run, paused, or removed, config file save or restore, settings backup download or restore, site theme change, Asterisk restart, reboot, user added, edited, or removed, password change, and login. Each entry has the user, node, target, a short detail, and the address it came from. Passwords and file contents are never stored. A failed login with an unknown username is recorded without the name, since that is often a password typed into the wrong box.

Entries are kept for 90 days (`action_days` in `[history]`) in `/var/lib/sm-elite/actions.db`.
