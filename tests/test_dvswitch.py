import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path

from smelite.dvswitch import DVSwitch, DVSwitchError, mode_ports, packet, parse_layout

LAYOUT = """
[node 1999]
abinfo = {abinfo}
dvswitch_ini = {ini}
modes = DMR, YSF

[network bm]
label = BrandMeister
password = hunter2
server = bm.example
port = 62031

[mode DMR]
BM Texas = @bm 3148
Local 9 = 9

[mode YSF]
Parrot = parrot.example:42020

[mode P25]
Parrot = 10
"""

DVSWITCH_INI = """
[DMR]
TXPort = 31100   ; to MMDVM_Bridge
RXPort = 31103
[YSF]
TXPort = 30100
RXPort = 30103
"""


class ParseTests(unittest.TestCase):
    def test_network_rows_build_hotspot_strings(self):
        layout = parse_layout(LAYOUT.format(abinfo="auto", ini="/x"))
        texas = layout.talkgroups["DMR"][0]
        self.assertEqual(texas.target, "hunter2@bm.example:62031!3148")
        self.assertEqual(texas.network, "BrandMeister")
        self.assertTrue(texas.secret)
        self.assertEqual(layout.bridges["1999"].modes, ["DMR", "YSF"])

    def test_quoted_targets_keep_spaces(self):
        layout = parse_layout('[mode DSTAR]\nUnlink = "       U"\n')
        self.assertEqual(layout.talkgroups["DSTAR"][0].target, "       U")

    def test_errors(self):
        with self.assertRaises(DVSwitchError):
            parse_layout("[mode DMR]\nX = @missing 91\n")
        with self.assertRaises(DVSwitchError):
            parse_layout("[mode FM]\nX = 1\n")
        with self.assertRaises(DVSwitchError):
            parse_layout("[network bm]\nserver = s\n[mode DMR]\nX = @bm 91\n")

    def test_packet_and_ports(self):
        self.assertEqual(packet("txTg=91"), b"\x05\x07txTg=91")
        with self.assertRaises(DVSwitchError):
            packet("x" * 300)
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "DVSwitch.ini"
            ini.write_text(DVSWITCH_INI)
            self.assertEqual(mode_ports(ini, "DMR"), (31100, 31103))
            with self.assertRaises(DVSwitchError):
                mode_ports(ini, "P25")


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(1)
        port = self.sock.getsockname()[1]
        self.abinfo = root / "ABInfo_34001.json"
        self.write_abinfo("DMR", "hunter2@bm.example:62031!3148", port)
        ini = root / "DVSwitch.ini"
        ini.write_text(DVSWITCH_INI)
        conf = root / "dvswitch.ini"
        conf.write_text(LAYOUT.format(abinfo=self.abinfo, ini=ini))
        self.dvs = DVSwitch(conf, tmp=root)

    async def asyncTearDown(self):
        self.sock.close()
        self.tmp.cleanup()

    def write_abinfo(self, mode, tune, port):
        self.abinfo.write_text(json.dumps({"ab": {"version": "1.6"}, "tlv": {"rx_port": port, "ambe_mode": mode}, "last_tune": tune}))

    def received(self, count):
        return [self.sock.recv(300)[2:].decode() for _ in range(count)]

    async def test_view_hides_secrets(self):
        view = self.dvs.view("1999")
        text = json.dumps(view)
        self.assertNotIn("hunter2", text)
        self.assertEqual([m["name"] for m in view["modes"]], ["DMR", "YSF"])
        self.assertEqual(view["status"]["tune"], "BM Texas (BrandMeister)")
        self.assertEqual(view["modes"][0]["talkgroups"][1]["target"], "9")
        self.assertEqual(view["modes"][0]["talkgroups"][0]["target"], "3148")

    async def test_tune_from_list_sends_full_target(self):
        await self.dvs.tune("1999", "DMR", "tester", index=0)
        self.assertEqual(self.received(2), ["txTg=hunter2@bm.example:62031!3148", "info"])

    async def test_tune_other_mode_switches_first(self):
        await self.dvs.tune("1999", "YSF", "tester", custom="other.example:42000")
        sent = self.received(7)
        self.assertEqual(sent[:6], ["rxport=30000", "message=Setting mode to YSF", "ambeMode=YSF",
                                    "txport=30103", "rxport=30100", "info"])
        self.assertEqual(sent[6], "txTg=other.example:42000")

    async def test_refusals(self):
        with self.assertRaises(DVSwitchError):
            await self.dvs.tune("1999", "P25", "tester", index=0)
        with self.assertRaises(DVSwitchError):
            await self.dvs.tune("1999", "DMR", "tester", custom="pw@host:1!91")
        with self.assertRaises(DVSwitchError):
            await self.dvs.tune("1999", "DMR", "tester", index=5)
        with self.assertRaises(DVSwitchError):
            self.dvs.view("2000")

    async def test_missing_abinfo_reports_offline(self):
        self.abinfo.unlink()
        self.assertFalse(self.dvs.status("1999")["online"])
        with self.assertRaises(DVSwitchError):
            await self.dvs.set_mode("1999", "DMR", "tester")


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_demo_attaches_to_demo_node(self):
        dvs = DVSwitch(Path(__file__).resolve().parent.parent / "config" / "dvswitch.ini", demo=True)
        self.assertEqual(dvs.nodes(), ["1999"])
        status = await dvs.tune("1999", "YSF", "tester", index=0)
        self.assertEqual((status["mode"], status["tune"]), ("YSF", "Parrot"))
        self.assertNotIn("your-hotspot-password", json.dumps(dvs.view("1999")))


if __name__ == "__main__":
    unittest.main()
