# Schedule

Operators open **Schedule** in the control bar to add an action: pick the node, the action (connect, monitor, local monitor, disconnect, disconnect all, play a saved sound, or a command from `commands.ini`), the time, and either the days of the week it repeats on or a single date. A Tuesday net might be a permanent connect to the net's hub at 19:55 and a disconnect at 21:05; a special event net can run once on its date.

- Times use the node's own clock and time zone, shown at the top of the dialog.
- Each action runs with the permissions of the user who added or last edited it, at the moment it runs. Removing that user, or limiting them to other nodes, stops their scheduled actions; the last run column then says why it was skipped.
- Operators see and manage the scheduled actions on the nodes they may control; admins see all of them.
- Edit loads an action back into the form; saving it turns the action on again and keeps its last run.
- Pause keeps an action without running it, and Run now runs it once straight away.
- A one-time action pauses itself after it runs. Edit it to give it a new date.
- An action whose time passes while SM-Elite is stopped is skipped; it runs again at its next time.
- Every run is in the [action log](action-log.md), with its result.
