"""Resume response validation and retry limits with no ASF traffic."""
from contextlib import redirect_stdout
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError
from sentinel_1_stack import slc as app
from test_slc import FakeResponse, FakeSession, feature


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.product = app.parse_product(feature(),1)
        self.partial = self.root/(self.product.filename+'.part')
        self.final = self.root/self.product.filename

    def fetch(self, session, **kwargs):
        with redirect_stdout(io.StringIO()), patch.object(app,'retry_pause'):
            return app.fetch_product(self.product,self.root,session,**kwargs)

    def test_existing_partial_resumes_and_verifies_full_md5(self):
        self.partial.write_bytes(b'da')
        session=FakeSession([FakeResponse([b'ta'],206,{'Content-Range':'bytes 2-3/4','Content-Length':'2'})])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertEqual(session.calls[0][1]['headers']['Range'],'bytes=2-')
        self.assertEqual(self.final.read_bytes(),b'data')

    def test_midstream_disconnect_retries_from_persisted_offset(self):
        session=FakeSession([FakeResponse([b'da',ChunkedEncodingError('secret-url')]),
                             FakeResponse([b'ta'],206,{'Content-Range':'bytes 2-3/4'})])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertEqual(session.calls[1][1]['headers']['Range'],'bytes=2-')
        self.assertEqual(self.final.read_bytes(),b'data')

    def test_ignored_range_restarts_without_duplicate_prefix(self):
        self.partial.write_bytes(b'da')
        self.assertEqual(self.fetch(FakeSession([FakeResponse([b'data'])])),'downloaded')
        self.assertEqual(self.final.read_bytes(),b'data')

    def test_invalid_content_range_preserves_partial_and_does_not_retry(self):
        for value in ('bytes 0-1/4','bytes 2-3/5','bytes 2-2/4','bytes 2-3/*','invalid', ''):
            with self.subTest(value=value):
                self.partial.write_bytes(b'da')
                session=FakeSession([FakeResponse([b'ta'],206,{'Content-Range':value})])
                with self.assertRaisesRegex(app.DownloadError,'Content-Range'):self.fetch(session)
                self.assertEqual(self.partial.read_bytes(),b'da')
                self.assertEqual(len(session.calls),1)
                self.assertFalse(self.final.exists())

    def test_retry_budget_is_bounded_and_auth_errors_are_not_retried(self):
        session=FakeSession([ConnectionError('signed-secret') for _ in range(4)])
        with self.assertRaises(app.DownloadError) as error:self.fetch(session)
        self.assertEqual(len(session.calls),4)
        self.assertNotIn('signed-secret',str(error.exception))
        for code in (401,403,404):
            session=FakeSession([FakeResponse(status=code)])
            with self.assertRaises(app.DownloadError):self.fetch(session)
            self.assertEqual(len(session.calls),1)

    def test_retryable_http_and_truncated_response(self):
        for code in (429,500,502,503,504):
            session=FakeSession([FakeResponse(status=code),FakeResponse([b'data'])])
            self.assertEqual(self.fetch(session),'downloaded')
            self.final.unlink()
        session=FakeSession([FakeResponse([b'da']),FakeResponse([b'ta'],206,{'Content-Range':'bytes 2-3/4'})])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertEqual(self.final.read_bytes(),b'data')

    def test_corrupt_prefix_restarts_after_md5_failure(self):
        self.partial.write_bytes(b'xx')
        session=FakeSession([FakeResponse([b'ta'],206,{'Content-Range':'bytes 2-3/4'}),FakeResponse([b'data'])])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertNotIn('Range',session.calls[1][1]['headers'])
        self.assertEqual(self.final.read_bytes(),b'data')

    def test_416_falls_back_and_oversized_partial_starts_fresh(self):
        self.partial.write_bytes(b'da')
        session=FakeSession([FakeResponse(status=416),FakeResponse([b'data'])])
        self.assertEqual(self.fetch(session),'downloaded')
        self.final.unlink();self.partial.write_bytes(b'oversized')
        session=FakeSession([FakeResponse([b'data'])])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertNotIn('Range',session.calls[0][1]['headers'])

    def test_full_body_before_connection_error_needs_no_new_request(self):
        session=FakeSession([FakeResponse([b'data',ChunkedEncodingError('cut trailer')])])
        self.assertEqual(self.fetch(session),'downloaded')
        self.assertEqual(len(session.calls),1)

    def test_wrong_length_or_encoding_does_not_modify_partial(self):
        for headers in ({'Content-Length':'99'}, {'Content-Encoding':'gzip'}):
            self.partial.write_bytes(b'da')
            session=FakeSession([FakeResponse([b'data'],headers=headers)])
            with self.assertRaises(app.DownloadError):self.fetch(session)
            self.assertEqual(self.partial.read_bytes(),b'da')

    def test_real_http_disconnect_and_range_resume(self):
        data=b'abcdefghijkl'
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                header=self.headers.get('Range');seen.append(header)
                if not header:
                    self.send_response(200);self.send_header('Content-Length',str(len(data)))
                    self.end_headers();self.wfile.write(data[:6]);self.wfile.flush();self.close_connection=True
                else:
                    start=int(header.split('=')[1].split('-')[0])
                    self.send_response(206);self.send_header('Content-Range',f'bytes {start}-{len(data)-1}/{len(data)}')
                    self.send_header('Content-Length',str(len(data)-start));self.end_headers();self.wfile.write(data[start:])
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.product=app.Product(self.product.filename,f'http://127.0.0.1:{server.server_port}/fixture',len(data),hashlib.md5(data).hexdigest())
        try:
            with requests.Session() as session, patch.object(app,'CHUNK_SIZE',2):
                self.assertEqual(self.fetch(session),'downloaded')
            self.assertEqual(seen,[None,'bytes=6-'])
            self.assertEqual(self.final.read_bytes(),data)
        finally:
            server.shutdown();server.server_close();worker.join()
