"""Offline behavior tests: no credentials and no requests to ASF."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sentinel_1_stack import slc as app
from sentinel_1_stack.workspace import initialize
from requests.exceptions import ConnectionError


class FakeResponse:
    def __init__(self, chunks=(), status=200):
        self.chunks = chunks
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class FakeSession:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def feature(name="S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip", content=b"data"):
    return {
        "type": "Feature",
        "properties": {
            "fileName": name,
            "url": f"https://datapool.asf.alaska.edu/SLC/SA/{name}",
            "bytes": str(len(content)),
            "md5sum": hashlib.md5(content).hexdigest(),
        },
    }


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.files = self.root / "files"
        self.files.mkdir()
        self.archive = self.root / "archive_files"
        self.work = self.root / "project"
        self.config = self.root / "project.yaml"
        self.config.write_text("work_dir: project\nschema_version: 1\n")
        with redirect_stdout(io.StringIO()):
            initialize(self.work)
        self.output = self.root / "SAR data"
        self.input = self.files / "scenes.geojson"
        self.logs = self.root / "logs"
        self.session = FakeSession()
        patcher = patch.object(app, "create_session", side_effect=lambda: self.session)
        self.factory = patcher.start()
        self.addCleanup(patcher.stop)

    def write_input(self, *features):
        self.input.write_text(json.dumps({"type": "FeatureCollection", "features": list(features)}))
        return self.input.read_bytes()

    def run_app(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = app.main([str(self.config), str(self.input), "--out", str(self.output), "--log-dir", str(self.logs), *args])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return status



    def test_dry_run_is_read_only(self):
        original = self.write_input(feature(), feature("S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip", b"12345"))
        before = set(self.root.rglob("*"))
        self.assertEqual(self.run_app("--dry-run"), 0)
        self.assertIn("製品数: 2", self.stdout)
        self.assertIn("9 bytes", self.stdout)
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertEqual(self.input.read_bytes(), original)
        self.factory.assert_not_called()

    def test_invalid_schema_is_rejected_before_network(self):
        documents = ["not json", "[]", '{"type":"FeatureCollection","features":[]}']
        for document in documents:
            with self.subTest(document=document):
                self.input.write_text(document)
                self.assertEqual(self.run_app(), 2)
                self.assertEqual(self.input.read_text(), document)
        self.factory.assert_not_called()

    def test_invalid_product_metadata_is_rejected(self):
        cases = [
            ("fileName", "../outside.zip"),
            ("fileName", "sub\\outside.zip"),
            ("url", "https://asf.alaska.edu.evil.example/file.zip"),
            ("url", "http://datapool.asf.alaska.edu/file.zip"),
            ("bytes", 1.5), ("bytes", True), ("bytes", 0),
            ("md5sum", "bad-md5"),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                item = feature()
                item["properties"][key] = value
                self.write_input(item)
                self.assertEqual(self.run_app(), 2)
        self.factory.assert_not_called()

    def test_missing_property_and_duplicate_names_are_rejected(self):
        item = feature()
        del item["properties"]["md5sum"]
        self.write_input(item)
        self.assertEqual(self.run_app(), 2)
        self.write_input(feature(), feature())
        self.assertEqual(self.run_app(), 2)
        self.factory.assert_not_called()


    def test_success_downloads_all_and_preserves_input(self):
        original = self.write_input(feature(), feature("S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip", b"other"))
        self.session = FakeSession([FakeResponse([b"da", b"", b"ta"]), FakeResponse([b"other"])])
        self.assertEqual(self.run_app(), 0)
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").read_bytes(), b"data")
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip").read_bytes(), b"other")
        self.assertFalse(list(self.output.glob("*.part")))
        self.assertTrue(self.input.exists())
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(self.archive.exists())
        self.assertEqual(self.session.calls[0][1]["timeout"], (30, 120))

    def test_verified_existing_file_is_skipped(self):
        self.write_input(feature())
        self.output.mkdir()
        (self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").write_bytes(b"data")
        self.assertEqual(self.run_app(), 0)
        self.assertEqual(self.session.calls, [])
        self.assertTrue(self.input.exists())

    def test_verified_partial_is_recovered_without_network(self):
        self.write_input(feature())
        self.output.mkdir()
        partial = self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip.part"
        partial.write_bytes(b"data")
        self.assertEqual(self.run_app(), 0)
        self.assertEqual(self.session.calls, [])
        self.assertFalse(partial.exists())
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").read_bytes(), b"data")

    def test_bad_md5_keeps_input_and_existing_final(self):
        original = self.write_input(feature())
        self.output.mkdir()
        (self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").write_bytes(b"old!")
        self.session = FakeSession([FakeResponse([b"evil"])])
        self.assertEqual(self.run_app("--replace-invalid"), 1)
        self.assertIn("MD5", self.stderr)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").read_bytes(), b"old!")
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip.part").read_bytes(), b"evil")
        self.assertFalse(self.archive.exists())

    def test_short_or_oversized_response_is_not_published(self):
        for content in (b"da", b"data-too-long"):
            with self.subTest(content=content):
                self.write_input(feature())
                self.session = FakeSession([FakeResponse([content])])
                self.assertEqual(self.run_app(), 1)
                self.assertTrue(self.input.exists())
                self.assertFalse((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").exists())
                self.assertFalse(self.archive.exists())

    def test_late_failure_and_rerun_skip_the_verified_first_product(self):
        original = self.write_input(feature("S1A_IW_SLC__1SDV_20171214T215256_20171214T215325_019529_021262_A26F.zip", b"one"), feature("S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip", b"two"))
        self.session = FakeSession([
            FakeResponse([b"one"]), FakeResponse([b"t", ConnectionError("offline")]),
        ])
        self.assertEqual(self.run_app(), 1)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171214T215256_20171214T215325_019529_021262_A26F.zip").read_bytes(), b"one")
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip.part").read_bytes(), b"t")
        self.assertFalse(self.archive.exists())
        self.session = FakeSession([FakeResponse([b"two"])])
        self.assertEqual(self.run_app(), 0)
        self.assertEqual(len(self.session.calls), 1)
        self.assertTrue(self.session.calls[0][0].endswith("S1A_IW_SLC__1SDV_20171208T215256_20171208T215325_019529_021262_A26F.zip"))
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(self.archive.exists())

    def test_http_auth_error_keeps_input(self):
        original = self.write_input(feature())
        self.session = FakeSession([FakeResponse(status=401)])
        self.assertEqual(self.run_app(), 1)
        self.assertIn(".netrc", self.stderr)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(self.archive.exists())

    def test_dns_failure_reports_cause_without_exposing_request_url(self):
        from urllib3.exceptions import MaxRetryError, NameResolutionError

        original = self.write_input(feature())
        dns_error = NameResolutionError(
            "datapool.asf.alaska.edu", None, socket.gaierror(-2, "Name or service not known")
        )
        wrapped = MaxRetryError(None, "/S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip?token=do-not-print", reason=dns_error)
        self.session = FakeSession([ConnectionError(wrapped) for _ in range(4)])
        with patch.object(app.time, "sleep") as sleep:
            self.assertEqual(self.run_app(), 1)
        self.assertEqual(len(self.session.calls), 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])
        self.assertIn("名前解決（DNS）", self.stderr)
        self.assertNotIn("do-not-print", self.stderr)
        self.assertNotIn("do-not-print", self.stdout)
        self.assertNotIn("do-not-print", next(self.logs.glob("*.log")).read_text())
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(self.archive.exists())
        self.assertFalse((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip.part").exists())

    def test_transient_dns_failure_retries_then_verifies_download(self):
        original = self.write_input(feature())
        self.session = FakeSession([
            ConnectionError(socket.gaierror(-5, "No address associated with hostname")),
            ConnectionError(socket.gaierror(-5, "No address associated with hostname")),
            FakeResponse([b"data"]),
        ])
        with patch.object(app.time, "sleep") as sleep:
            self.assertEqual(self.run_app(), 0)
        self.assertEqual(len(self.session.calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])
        self.assertEqual(self.input.read_bytes(), original)
        self.assertEqual((self.output / feature()["properties"]["fileName"]).read_bytes(), b"data")
        self.assertIn("DNS 再試行 2/3", next(self.logs.glob("*.log")).read_text())
        self.assertEqual(json.loads(next(self.logs.glob("*.json")).read_text())["status"], "complete")

    def test_non_dns_connection_failure_is_not_retried(self):
        self.write_input(feature())
        self.session = FakeSession([ConnectionError("connection refused")])
        with patch.object(app.time, "sleep") as sleep:
            self.assertEqual(self.run_app(), 1)
        sleep.assert_not_called()
        self.assertEqual(len(self.session.calls), 1)

    def test_interrupt_during_dns_retry_keeps_input_without_partial(self):
        original = self.write_input(feature())
        self.session = FakeSession([ConnectionError(socket.gaierror(-5, "DNS failure"))])
        with patch.object(app.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_app(), 130)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(list(self.output.glob("*.part")))

    def test_keyboard_interrupt_keeps_input_and_partial(self):
        original = self.write_input(feature())
        self.session = FakeSession([FakeResponse([b"da", KeyboardInterrupt()])])
        self.assertEqual(self.run_app(), 130)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertEqual((self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip.part").read_bytes(), b"da")
        self.assertFalse(self.archive.exists())


    def test_changed_input_is_detected(self):
        self.write_input(feature())
        batch = app.load_batch(self.input)
        self.write_input(feature("different.zip"))
        with self.assertRaises(app.DownloadError):
            app.ensure_input_unchanged(batch)
        self.assertTrue(self.input.exists())
        self.assertFalse(self.archive.exists())



    def test_output_symlink_is_not_followed(self):
        self.write_input(feature())
        self.output.mkdir()
        other = self.root / "important.txt"
        other.write_bytes(b"keep")
        (self.output / "S1A_IW_SLC__1SDV_20171202T215256_20171202T215325_019529_021262_A26F.zip").symlink_to(other)
        self.assertEqual(self.run_app(), 1)
        self.assertEqual(other.read_bytes(), b"keep")
        self.assertEqual(self.session.calls, [])
        self.assertTrue(self.input.exists())

    def test_missing_input_and_input_symlink_are_rejected(self):
        self.assertEqual(self.run_app(), 2)
        self.factory.assert_not_called()
        self.assertFalse(self.output.exists())
        real = self.files / "real.geojson"
        real.write_text('{}')
        self.input.symlink_to(real)
        self.assertEqual(self.run_app(), 2)

    def test_explicit_input_allows_other_json_files(self):
        self.write_input(feature())
        (self.files / "other.json").write_text("invalid JSON")
        self.assertEqual(self.run_app("--dry-run"), 0)

    def test_grd_and_non_sentinel_products_rejected(self):
        for name in ("image.zip", feature()["properties"]["fileName"].replace("SLC__", "GRDH_")):
            self.write_input(feature(name))
            self.assertEqual(self.run_app(), 2)
        self.factory.assert_not_called()

    def test_bad_existing_zip_requires_explicit_replacement(self):
        self.write_input(feature())
        self.output.mkdir()
        destination = self.output / feature()["properties"]["fileName"]
        destination.write_bytes(b"old!")
        self.assertEqual(self.run_app(), 1)
        self.assertIn("--replace-invalid", self.stderr)
        self.assertEqual(self.session.calls, [])
        self.assertEqual(destination.read_bytes(), b"old!")
        self.session = FakeSession([FakeResponse([b"data"])])
        self.assertEqual(self.run_app("--replace-invalid"), 0)
        self.assertEqual(destination.read_bytes(), b"data")

    def test_output_and_input_locks_block_concurrent_writers(self):
        self.write_input(feature())
        self.output.mkdir()
        with app.batch_lock(self.output):
            self.assertEqual(self.run_app(), 1)
        with app.input_lock(self.input):
            self.assertEqual(self.run_app(), 1)
        self.factory.assert_not_called()

    def test_manifest_and_transcript_record_success(self):
        original = self.write_input(feature())
        self.session = FakeSession([FakeResponse([b"data"])])
        self.assertEqual(self.run_app(), 0)
        manifest = json.loads(next(self.logs.glob("*.json")).read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["input_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest["products"][0]["status"], "downloaded")
        self.assertEqual(self.input.read_bytes(), original)
        self.assertNotIn("archive", manifest)
        self.assertIn("検証成功", next(self.logs.glob("*.log")).read_text())

    def test_manifest_and_transcript_record_failure(self):
        self.write_input(feature())
        self.session = FakeSession([FakeResponse(status=403)])
        self.assertEqual(self.run_app(), 1)
        manifest = json.loads(next(self.logs.glob("*.json")).read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertNotIn("archive", manifest)
        self.assertIn("ERROR:", next(self.logs.glob("*.log")).read_text())

    def test_insufficient_space_does_not_start_download(self):
        self.write_input(feature())
        with patch.object(app.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10, 10, 0)):
            self.assertEqual(self.run_app(), 1)
        self.assertEqual(self.session.calls, [])
        self.assertTrue(self.input.exists())

    def test_cli_module_works_outside_project(self):
        from sentinel_1_stack import cli
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.write_input(feature())
            self.assertEqual(cli.main(["download", str(self.config), str(self.input), "--out", str(self.output), "--dry-run"]), 0)

    def test_default_output_adds_slcs_and_keeps_read_only_json(self):
        original = self.write_input(feature())
        self.input.chmod(0o444)
        destination = self.work / "input/slc"
        existing = destination / "existing.zip"
        existing.write_bytes(b"keep")
        self.session = FakeSession([FakeResponse([b"data"])])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([str(self.config), str(self.input)]), 0)
        self.assertEqual((destination / feature()["properties"]["fileName"]).read_bytes(), b"data")
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(self.input.read_bytes(), original)
        self.assertFalse(list(self.root.rglob("archive_files")))
        self.assertTrue(list((self.work / "logs/downloads").glob("*.log")))

    def test_uninitialized_work_fails_without_side_effects(self):
        self.write_input(feature())
        empty = self.root / "not-initialized"
        self.config.write_text("work_dir: not-initialized\nschema_version: 1\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(app.main([str(self.config), str(self.input)]), 2)
        self.assertFalse(empty.exists())
        self.factory.assert_not_called()

    def test_work_root_is_relative_to_config_not_current_directory(self):
        self.write_input(feature())
        with patch("os.getcwd", return_value="/tmp"), redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([str(self.config), str(self.input), "--dry-run"]), 0)



if __name__ == "__main__":
    unittest.main()
