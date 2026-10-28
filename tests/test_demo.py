import http.client
import json
import threading
import unittest

from ops.demo import make_demo_server


class DemoTests(unittest.TestCase):
    def test_generated_snapshot_is_local_and_filtered(self):
        server, token = make_demo_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            connection.request("GET", "/api/wall/snapshot",
                               headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertEqual(body["snapshot"]["source"], "Authenticated observer API")
            self.assertFalse(body["snapshot"]["guests"][0]["controllable"])
            self.assertNotIn("password", body["snapshot"]["guests"][0])
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.shutdown()
            server.server_close()
            server.store.db.close()
            server.demo_temp.cleanup()


if __name__ == "__main__":
    unittest.main()
