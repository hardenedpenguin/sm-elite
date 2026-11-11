# DVSwitch

For a node with a DVSwitch bridge (Analog_Bridge and MMDVM_Bridge), edit `/etc/sm-elite/dvswitch.ini` (the package puts a commented copy there):

```ini
[node 1999]
abinfo = /tmp/ABInfo_34001.json

[network brandmeister]
label = BrandMeister
password = your-hotspot-password
server = 3102.master.brandmeister.network
port = 62031

[mode DMR]
BM Texas = @brandmeister 3148
BM Parrot = @brandmeister 9990#

[mode YSF]
America Link = americalink.hamfm.com:42000
```

A DVSwitch button then appears in the control bar for that node. SM-Elite talks to Analog_Bridge directly on the control port listed in its ABInfo file, the same messages `dvswitch.sh` sends, so it needs no shell scripts. Hotspot passwords stay in `dvswitch.ini` (readable only by root and the dashboard's group); the browser sees talkgroup names only, and talkgroups typed by hand cannot contain a password. Operators can switch modes and talkgroups; admins can also restart the `mmdvm_bridge*` and `analog_bridge*` services (second instances such as `analog_bridge2` included).

**Coming from supermon-ng:** when `/etc/supermon-ng/user_files/dvswitch_config.yml` exists, installing or upgrading the package writes `dvswitch.ini` from it: credential profiles become `[network]` sections, and every mode's talkgroups carry over. Each node with `rxchannel = USRP/127.0.0.1:<port>:...` in rpt.conf gets a `[node N]` section pointing at `/tmp/ABInfo_<port>.json` and the DVSwitch.ini next to the matching Analog_Bridge.ini (for example `/etc/dvswitch/2/` for a second bridge). This happens once, and only while `dvswitch.ini` has no `[node N]` section; the previous file is kept as `dvswitch.ini.before-import`. Per-node `dvswitch_config_<node>.yml` lists are not imported.
