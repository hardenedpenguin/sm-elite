# Announce

Operators open **Announce** in the control bar to type text and play it with Piper text-to-speech, on this node's radio only or on this node and every connected link. Announcements can be saved and replayed later, and a saved one can also be played by a [scheduled action](schedule.md).

Text-to-speech needs `piper` and `sox` on the node and at least one voice in `/var/lib/piper-tts`. Without them the Announce dialog still plays saved sounds. The paths, voices folder, and the longest text allowed are in the `[sounds]` section of [`smelite.ini`](configuration.md).
