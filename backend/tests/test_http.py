from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

temporary_database = tempfile.TemporaryDirectory()
os.environ["RELAYLAB_DB"] = str(Path(temporary_database.name) / "http.sqlite3")
sys.path.insert(0, str(Path(__file__).parents[1]))
import server


class HttpBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        temporary_database.cleanup()

    def post(self, path: str, raw_body: str) -> tuple[int, dict]:
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port)
        connection.request("POST", path, raw_body, {"Content-Type": "application/json", "Origin": "http://localhost:5179"})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        return response.status, body

    def test_non_string_receiver_returns_structured_400(self) -> None:
        status, body = self.post("/api/deliveries", '{"idempotencyKey":"bad-receiver","receiver":[],"payload":{"event":"bad"}}')
        self.assertEqual(400, status)
        self.assertEqual("invalid_request", body["error"])

    def test_nonfinite_json_number_returns_structured_400(self) -> None:
        status, body = self.post("/api/clock/advance", '{"seconds":NaN}')
        self.assertEqual(400, status)
        self.assertEqual("invalid_request", body["error"])

    def test_non_string_worker_returns_structured_400(self) -> None:
        status, body = self.post("/api/worker/run", '{"worker":14}')
        self.assertEqual(400, status)
        self.assertEqual("invalid_request", body["error"])


if __name__ == "__main__":
    unittest.main()
