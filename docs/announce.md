# Announce

Operators open **Announce** in the control bar to type text and play it with Piper text-to-speech, or to play a saved sound, on this node's radio only or on this node and every connected link. A saved sound can also be played by a [scheduled action](schedule.md).

**When** picks how it plays:

- **Polite** (the default) waits until nobody is talking on the node, neither on its own receiver nor on a link, and plays once it has been quiet for a moment. If the node stays busy for 5 minutes it gives up, and the [action log](action-log.md) says so. Only one announcement can wait per node at a time.
- **Priority** plays right away, on top of whoever is talking.

## Saving and uploading sounds

Admins can save typed text as an announcement, or upload their own recording: pick an MP3 or WAV file (up to 20 MB and 10 minutes), give it a name of lowercase letters, numbers, `-`, and `_`, and press **Upload**. Both kinds land in `/usr/local/share/asterisk/sounds/announcements/` as 8 kHz mu-law, show up in the list of saved sounds, and can be deleted there. Uploading a name that already exists replaces it after asking.

Uploads are converted by `sox` as the dashboard's own account, with limits on CPU time, memory, and output size, so a broken or hostile file cannot reach anything beyond that conversion. Only the converted audio goes through the [root helper](admin.md#root-access) into the sounds folder. Each upload is recorded in the action log with the original file name.

## Requirements

- Uploads need `sox` and `libsox-fmt-mp3` (for MP3). The package recommends both, so a normal `apt install` brings them in.
- Text-to-speech needs `piper` and `sox` and at least one voice in `/var/lib/piper-tts`. Without them the Announce dialog still plays and uploads sounds.

The paths, voices folder, and the longest text allowed are in the `[sounds]` section of [`smelite.ini`](configuration.md).
