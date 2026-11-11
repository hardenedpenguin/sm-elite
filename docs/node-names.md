# Private node names (asdb.txt)

Private nodes, IRLP or EchoLink gateways, and anything else missing from the AllStar node directory show up as a bare number. Give them a name in `/etc/sm-elite/asdb.txt`, one node per line:

```
node|callsign|description|location
```

For example:

```
1999|W5GLE|Shop radio|Alvin, Texas
80016|NEWSLINE|[IRLP EXP0016]|Amateur Radio Newsline
89990|REF9990|[IRLP 9990]|ECHO Reflector
```

- Lines starting with `;` or `#` are comments. The package installs the file with a commented example.
- Entries here win over the AllStar directory, so you can also rename a public node.
- The names appear in node cards, favorites, Find a node, the Who can connect lists, and last heard (from the next keyup on; earlier entries keep the name they were saved with).
- Edit it on the Admin page (Config files, then `asdb.txt`); the names apply as soon as you save, and the previous version is backed up. From a shell, edit it as root (`sudo nano /etc/sm-elite/asdb.txt`); SM-Elite reads it again on the next directory refresh (every 15 minutes, `refresh_minutes` in `[database]`), or right away after `sudo systemctl restart sm-elite`.
- Coming from Supermon or supermon-ng: `privatenodes.txt` uses the same format, so its lines can be copied in as they are.
