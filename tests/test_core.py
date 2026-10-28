import unittest
from api.core import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()

    def tearDown(self):
        self.store.db.close()

    def test_tenant_isolation(self):
        item = self.store.create("acme", "tech", "tickets", {"title": "test"})
        self.assertEqual(len(self.store.list("acme", "tickets")), 1)
        self.assertEqual(self.store.list("other", "tickets"), [])
        self.assertIsNone(self.store.get("other", "tickets", item["id"]))

    def test_approval_separation(self):
        action = self.store.create("acme", "alice", "actions", {"requested_by": "alice", "command": "restart"})
        with self.assertRaises(ValueError):
            self.store.approve("acme", "alice", action["id"])
        approved = self.store.approve("acme", "bob", action["id"])
        self.assertEqual(approved["state"], "approved")
        with self.assertRaises(ValueError):
            self.store.approve("acme", "carol", action["id"])

    def test_audit(self):
        self.store.create("acme", "alice", "documents", {"title": "SOP"})
        self.assertEqual(self.store.audit("acme")[0]["verb"], "create")


if __name__ == "__main__":
    unittest.main()
