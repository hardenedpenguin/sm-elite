import os
import tempfile
import unittest
from pathlib import Path

from smelite.auth import AuthStore
from smelite.config import load_settings


class AuthTests(unittest.TestCase):
    def test_roles_sessions_and_node_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AuthStore(Path(tmp) / "auth.db")
            self.assertEqual(store.user_count(), 0)
            store.create_user("Ada", "long-secret", "admin")
            store.create_user("ops", "long-secret", "operator", ["1998"])
            self.assertIsNone(store.verify("Ada", "nope"))
            admin = store.verify("Ada", "long-secret")
            self.assertTrue(admin.can_command("1999"))
            operator = store.verify("ops", "long-secret")
            self.assertTrue(operator.can_command("1998"))
            self.assertFalse(operator.can_command("1999"))
            token = store.create_session(operator)
            self.assertEqual(store.user_for_token(token).name, "ops")
            store.drop_session(token)
            self.assertIsNone(store.user_for_token(token))
            with self.assertRaises(ValueError):
                store.create_user("x", "short", "viewer")
            store.close()


class ConfigTests(unittest.TestCase):
    def test_example_config_loads(self):
        root = Path(__file__).resolve().parents[1]
        settings = load_settings(root / "config" / "smelite.ini")
        self.assertTrue(settings.demo)
        self.assertEqual(settings.port, 8420)
        self.assertTrue(any(item.command.startswith("rpt stats") for item in settings.commands))
        self.assertEqual(settings.public, "read")
        self.assertTrue((settings.favorites_dir / "favorites.ini").is_file())


if __name__ == "__main__":
    unittest.main()
