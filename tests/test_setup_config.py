import configparser
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

from smelite.dvswitch import base_mode, parse_layout

SETUP = Path(__file__).resolve().parent.parent / "deploy" / "setup_config.py"
spec = importlib.util.spec_from_file_location("setup_config", SETUP)
setup_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_config)

RPT_CONF = """
[general]
[nodes]
546050 = radio@127.0.0.1/546050,NONE

[node-main](!)
duplex = 1

[546050](node-main)
rxchannel = SimpleUSB/546050

[546051](node-main)
"""

MANAGER_CONF = """
; comment
[general]
enabled = yes
port = 5038 ; inline comment
bindaddr = 127.0.0.1
#include "custom.conf"

[admin]
secret = s3cret;x
read = all
write = all
"""


class SetupConfigTests(unittest.TestCase):
    def test_detect_nodes(self):
        self.assertEqual(setup_config.detect_nodes(RPT_CONF), ["546050", "546051"])

    def test_detect_callsign(self):
        text = "[node-main](!)\nidrecording = |iNOTSET   ; id\n[546050](node-main)\nidrecording = |iw5gle\n"
        self.assertEqual(setup_config.detect_callsign(text), "W5GLE")
        self.assertEqual(setup_config.detect_callsign("[546050]\nidrecording = /var/lib/id\n"), "")

    def test_detect_ami(self):
        self.assertEqual(setup_config.detect_ami(MANAGER_CONF), {"port": 5038, "user": "admin", "secret": "s3cret"})
        self.assertIsNone(setup_config.detect_ami("[general]\nenabled = no\n[admin]\nsecret = x\n"))
        self.assertIsNone(setup_config.detect_ami("[general]\nenabled = yes\n"))
        other = setup_config.detect_ami("[general]\nport = 5039\n[ops](tpl)\nsecret = y\n")
        self.assertEqual(other, {"port": 5039, "user": "ops", "secret": "y"})

    def test_generated_files_parse(self):
        nodes = configparser.ConfigParser(interpolation=None)
        nodes.read_string(setup_config.nodes_ini(["546050"], {"port": 5038, "user": "admin", "secret": "p%w"}))
        self.assertEqual(nodes["546050"]["pass"], "p%w")
        ini = configparser.ConfigParser(interpolation=None)
        ini.read_string(setup_config.smelite_ini(
            listen=["127.0.0.1", "10.8.0.7"], port=8420, data_dir=Path("/var/lib/sm-elite"),
            favorites_dir=Path("/etc/smelite"), callsign="W5GLE", location="Alvin, Texas", admin=True,
            services=["asterisk", "sm-elite"], helper="/usr/lib/sm-elite/smelite-admin",
        ))
        self.assertEqual(ini["admin"]["helper"], "/usr/lib/sm-elite/smelite-admin")
        self.assertEqual(ini["server"]["listen"], "127.0.0.1, 10.8.0.7")
        self.assertEqual(ini["server"]["demo"], "no")
        self.assertEqual(ini["admin"]["enabled"], "yes")
        self.assertEqual(ini["database"]["file"], "/var/lib/sm-elite/astdb.txt")


SUPERMON_YAML = """
credentials:
  brandmeister:
    password: "bm-secret"
    server: "3102.repeater.net"
    port: 62031
modes:
  - DMR:
      talkgroups:
        - tgid: "9990#"
          alias: PARROT
        - tgid: "tgif-secret@tgif.network:62031!3100"
          alias: TGIF - USA
        - profile: brandmeister
          tg: "3102"
          alias: BM Texas
          network: BrandMeister
        - profile: brandmeister
          tg: 91
          alias: BM Texas
  - YSF:
      talkgroups:
        - tgid: "americalink.hamfm.com:42000"
          alias: US-AMERICA-LINK
  - DSTAR:
      talkgroups:
        - tgid: "       U"
          alias: Unlink
  - M17:
      talkgroups:
        - tgid: "M17-USA A"
"""

BRIDGE_RPT = """
[546051](node-main)
rxchannel = USRP/127.0.0.1:34001:32001
[546055](node-main)
rxchannel = SimpleUSB/546055
; rxchannel = USRP/127.0.0.1:34099:32099
[546056](node-main)
rxchannel = USRP/127.0.0.1:34021:32021   ; second bridge
"""


class DVSwitchImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "one").mkdir()
        (root / "two").mkdir()
        (root / "one" / "Analog_Bridge.ini").write_text("[USRP]\ntxPort = 32001\nrxPort = 34001 ; from asterisk\n")
        (root / "two" / "Analog_Bridge.ini").write_text("include = dvsm.macro\n\n[USRP]\nrxPort = 34021\n")
        (root / "two" / "DVSwitch.ini").write_text("[DMR]\nTXPort = 31103\nRXPort = 31100\n")
        self.inis = [root / "one" / "Analog_Bridge.ini", root / "two" / "Analog_Bridge.ini"]
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_bridges_from_usrp_channels(self):
        bridges = setup_config.detect_bridges(BRIDGE_RPT, self.inis)
        self.assertEqual(bridges, [
            {"node": "546051", "abinfo": "/tmp/ABInfo_34001.json", "dvswitch_ini": "/opt/MMDVM_Bridge/DVSwitch.ini"},
            {"node": "546056", "abinfo": "/tmp/ABInfo_34021.json", "dvswitch_ini": str(self.root / "two" / "DVSwitch.ini")},
        ])

    def test_supermon_yaml_becomes_a_layout(self):
        source = self.root / "dvswitch_config.yml"
        source.write_text(SUPERMON_YAML)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            text = setup_config.import_dvswitch(source, BRIDGE_RPT, self.inis)
        self.assertNotIn("secret", out.getvalue())
        self.assertIn("M17", out.getvalue())
        layout = parse_layout(text)
        self.assertEqual(sorted(layout.bridges), ["546051", "546056"])
        self.assertEqual(layout.bridges["546056"].abinfo, "/tmp/ABInfo_34021.json")
        dmr = {row.alias: row for row in layout.talkgroups["DMR"]}
        self.assertEqual(dmr["PARROT"].target, "9990#")
        self.assertEqual(dmr["TGIF - USA"].target, "tgif-secret@tgif.network:62031!3100")
        self.assertEqual(dmr["BM Texas"].target, "bm-secret@3102.repeater.net:62031!3102")
        self.assertEqual(dmr["BM Texas"].network, "BrandMeister")
        self.assertEqual(dmr["BM Texas (2)"].target, "bm-secret@3102.repeater.net:62031!91")
        self.assertEqual(layout.talkgroups["DSTAR"][0].target, "       U")
        self.assertNotIn("M17", layout.talkgroups)

    def test_only_unused_files_are_replaced(self):
        path = self.root / "dvswitch.ini"
        self.assertTrue(setup_config.dvswitch_unused(path))
        path.write_text((Path(__file__).resolve().parent.parent / "config" / "dvswitch.ini").read_text())
        self.assertTrue(setup_config.dvswitch_unused(path))
        path.write_text("[node 546051]\nabinfo = auto\n")
        self.assertFalse(setup_config.dvswitch_unused(path))
        path.write_text(setup_config.dvswitch_ini([], {}, {}, Path("x.yml")))
        self.assertFalse(setup_config.dvswitch_unused(path))

    def test_ysf_variants_count_as_ysf(self):
        self.assertEqual(base_mode("YSFN"), "YSF")
        self.assertEqual(base_mode("ysfw"), "YSF")
        self.assertEqual(base_mode("DMR"), "DMR")


if __name__ == "__main__":
    unittest.main()
