import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import serve


class ServeHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data = Path(self.temp_dir.name)
        self.data_patch = patch.object(serve, "DATA", self.data)
        self.data_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.data_patch.stop()
        self.temp_dir.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as response:
            return response.status, response.read()

    def test_live_health_does_not_require_digest(self):
        status, body = self.get("/health/live")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_ready_health_requires_digest(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.get("/health/ready")
        self.assertEqual(error.exception.code, 503)

        (self.data / "digest.html").write_text("<!doctype html><title>test</title>")
        status, body = self.get("/health/ready")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ready"})


if __name__ == "__main__":
    unittest.main()
