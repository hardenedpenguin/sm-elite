import tempfile
import unittest
from pathlib import Path

from smelite.parser import (
    build_snapshot,
    node_status,
    parse_sawstat,
    parse_voter,
    parse_xstat,
    styled_lines,
    temperature,
    tx_state,
)


def fixed_conn(name, peer, reconnects, direction, elapsed, state):
    return (
        f"Conn: {name:<10}{peer:<20}{str(reconnects):<12}{direction:<11}{elapsed:<20}{state:<20}"
    )


XSTAT = "\r\n".join([
    "Response: Success",
    fixed_conn("27339", "192.0.2.10", 0, "OUT", "01:12:04", "ESTABLISHED"),
    fixed_conn("3000001", "", 0, "IN", "00:02:11", "ESTABLISHED"),
    "LinkedNodes: T27339, R28404, C2000",
    "Var: RPT_RXKEYED=0",
    "Var: RPT_TXKEYED=1",
    "Var: RPT_TXEKEYED=0",
    "Var: cpu_temp=51.4",
    "Var: WX=72F",
    "Var: ALERT=None",
    "",
])

SAW = "\r\n".join([
    "Response: Success",
    "Conn: 27339 1 3 9",
    "Conn: 3000001 0 -1 -1",
    "",
])

OUTPUT_WRAPPED = "\r\n".join([
    "Response: Success",
    "Output: " + fixed_conn("55553", "10.1.1.5", 2, "IN", "00:00:04", "CONNECTING"),
    "Output: Var: RPT_RXKEYED=1",
    "Output: Var: RPT_TXKEYED=1",
    "Output: LinkedNodes: T55553",
    "",
])


class ParserTests(unittest.TestCase):
    def test_fixed_width_conn_and_echolink_without_peer(self):
        parsed = parse_xstat(XSTAT)
        east = parsed["connections"]["27339"]
        self.assertEqual(east["ip"], "192.0.2.10")
        self.assertEqual(east["reconnects"], 0)
        self.assertEqual(east["direction"], "OUT")
        self.assertEqual(east["state"], "ESTABLISHED")
        self.assertEqual(east["mode"], "Transceive")
        echo = parsed["connections"]["3000001"]
        self.assertIsNone(echo["ip"])
        self.assertEqual(echo["direction"], "IN")
        self.assertEqual(parsed["vars"]["cpu_temp"], "51.4")
        self.assertEqual(parsed["linked"], ["27339", "28404", "2000"])

    def test_output_prefixed_lines(self):
        parsed = parse_xstat(OUTPUT_WRAPPED)
        self.assertIn("55553", parsed["connections"])
        self.assertEqual(parsed["connections"]["55553"]["ip"], "10.1.1.5")
        self.assertEqual(parsed["connections"]["55553"]["state"], "CONNECTING")
        self.assertEqual(parsed["vars"]["RPT_RXKEYED"], "1")

    def test_sawstat_and_snapshot_order(self):
        saw = parse_sawstat(SAW)
        self.assertTrue(saw["27339"]["ptt"])
        self.assertEqual(saw["27339"]["since_key"], 3)
        snap = build_snapshot(
            "1998",
            XSTAT,
            SAW,
            desc="Demo",
            database={"27339": {"call": "W2CRA", "desc": "East Coast Reflector", "location": "New York"}},
        )
        self.assertEqual(snap["conns"][0]["node"], "27339")
        self.assertTrue(snap["conns"][0]["ptt"])
        self.assertIn("W2CRA", snap["conns"][0]["desc"])
        self.assertEqual(snap["remote_keyed_node"], "27339")
        self.assertEqual(snap["vars"]["WX"], "72F")
        self.assertEqual(tx_state(snap), "network")
        snap["rx_keyed"] = True
        self.assertEqual(tx_state(snap), "local")
        snap["rx_keyed"] = False
        snap["remote_keyed"] = False
        snap["tx_keyed"] = True
        self.assertEqual(tx_state(snap), "telemetry")

    def test_voter_json(self):
        raw = "\r\n".join([
            "Client: North",
            "RSSI: 255",
            "Client: Mix bus",
            "RSSI: 40",
            "Client: West Inactive",
            "RSSI: 0",
            "Voted: North",
            "",
        ])
        payload = parse_voter(raw)
        self.assertEqual(payload["voted"], "North")
        roles = {item["name"]: item["role"] for item in payload["clients"]}
        self.assertEqual(roles["North"], "voted")
        self.assertEqual(roles["Mix bus"], "mix")
        self.assertEqual(roles["West Inactive"], "inactive")
        self.assertEqual(payload["clients"][0]["percent"], 100)

    def test_supermon_status_variables(self):
        variables = {
            "ALERT": (
                "<span style='color: SpringGreen;'><b><u><a href='https://github.com/hardenedpenguin/SkywarnPlus-NG' "
                "style='color: inherit; text-decoration: none;'>SkywarnPlus-NG</a> Enabled</u></b></span>"
                "<br><span style='color: #FF0000;'>No Alerts</span>"
            ),
            "DISK": "Disk - 7.9G 15% used, 48G remains",
            "WX": "<b>Alvin, Texas   (63°F, 17°C / Clear)</b>",
            "cpu_temp": (
                "<span style='background-color:lightgreen;'><b>"
                "<span style='color: black; font-weight: bold;'>125 F</span></b></span>"
            ),
            "cpu_load": "Load Average: 0.11, 0.13, 0.12",
            "cpu_up": "Up 8 hours, 16 minutes",
        }
        status = node_status(variables)
        self.assertEqual(status["alert"], [
            {"text": "SkywarnPlus-NG Enabled", "color": "SpringGreen"},
            {"text": "No Alerts", "color": "#FF0000"},
        ])
        self.assertEqual(status["wx"], "Alvin, Texas (63°F, 17°C / Clear)")
        self.assertEqual(status["temp"], {"text": "125 F", "level": "ok"})
        self.assertEqual(status["uptime"], "Up 8 hours, 16 minutes")
        self.assertEqual(temperature("150 F")["level"], "warm")
        self.assertEqual(temperature("61.5")["level"], "hot")
        evil = styled_lines("<span style='color: red;background:url(x)'>x</span><br><span style='color: expression(1)'>y</span>")
        self.assertEqual(evil, [{"text": "x", "color": "red"}, {"text": "y"}])


if __name__ == "__main__":
    unittest.main()
