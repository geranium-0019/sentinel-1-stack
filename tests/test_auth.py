"""Exercise the real ASFSession redirects with fake credentials and no network."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter

from sentinel_1_stack.slc import create_session


class RedirectAdapter(BaseAdapter):
    def __init__(self):
        self.seen = []

    def send(self, request, **kwargs):
        self.seen.append(request)
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.raw = io.BytesIO(b"data")
        response._content = b"data"
        if len(self.seen) == 1:
            response.status_code = 302
            response.headers["Location"] = "https://urs.earthdata.nasa.gov/login"
        elif len(self.seen) == 2:
            response.status_code = 302
            response.headers["Location"] = "https://datapool.asf.alaska.edu/product.zip"
        else:
            response.status_code = 200
        return response

    def close(self):
        pass


class AuthenticationTests(unittest.TestCase):
    def test_explicit_netrc_is_used_during_earthdata_redirect(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.netrc"
            path.write_text("machine urs.earthdata.nasa.gov login fake-user password fake-password\n")
            path.chmod(0o600)
            with patch.dict(os.environ, {"NETRC": str(path)}), create_session() as session:
                adapter = RedirectAdapter()
                session.mount("https://", adapter)
                result = session.get("https://datapool.asf.alaska.edu/product.zip")
                self.assertEqual(result.status_code, 200)
                self.assertNotIn("Authorization", adapter.seen[0].headers)
                self.assertTrue(adapter.seen[1].headers["Authorization"].startswith("Basic "))
                self.assertEqual(session.headers["Accept-Encoding"], "identity")
                self.assertTrue(session.verify)

    def test_auth_is_removed_for_untrusted_redirect(self):
        with create_session() as session:
            session.trust_env = False
            request = requests.Request("GET", "https://untrusted.example/file",
                                       headers={"Authorization": "Bearer fake-token"}).prepare()
            response = requests.Response()
            response.request = requests.Request("GET", "https://urs.earthdata.nasa.gov/login").prepare()
            session.rebuild_auth(request, response)
            self.assertNotIn("Authorization", request.headers)
