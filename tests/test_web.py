import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest

from app import Handler


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, obj=None, headers=None):
        port = self.server.server_port
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        body = json.dumps(obj).encode() if obj is not None else None
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        result = response.status, response.read(), response.getheader("Content-Security-Policy")
        conn.close()
        return result

    def test_demo_and_security_headers(self):
        status, body, policy = self.request("POST", "/api/demo", {"top_k": 2}, {"Content-Type": "application/json", "X-Audit-Request": "1"})
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["records"]), 24)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_foreign_origin_and_missing_header_are_rejected(self):
        status, _, _ = self.request("POST", "/api/demo", {}, {"Content-Type": "application/json", "X-Audit-Request": "1", "Origin": "https://other.example"})
        self.assertEqual(status, 403)
        self.assertEqual(self.request("POST", "/api/demo", {}, {"Content-Type": "application/json"})[0], 403)

    def test_malformed_and_import_validation(self):
        headers = {"Content-Type": "application/json", "X-Audit-Request": "1"}
        self.assertEqual(self.request("POST", "/api/demo", {"top_k": 0}, headers)[0], 400)
        self.assertEqual(self.request("POST", "/api/import", {"jsonl": "{broken"}, headers)[0], 400)
        self.assertEqual(self.request("GET", "/missing")[0], 404)
        self.assertEqual(self.request("GET", "/")[0], 200)


if __name__ == "__main__": unittest.main()
