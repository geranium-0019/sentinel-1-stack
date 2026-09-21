"""Offline orbit selection, XML integrity, and failure behavior."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import requests

from sentinel_1_stack import orbit
from sentinel_1_stack.cli import main
from sentinel_1_stack.workspace import initialize

SCENE = "S1D_IW_SLC__1SDV_20260903T112235_20260903T112306_004412_0082B1_61FC"
POE = "S1D_OPER_AUX_POEORB_OPOD_20260923T120000_V20260903T110000_20260903T114000.EOF"
RES = POE.replace("POEORB", "RESORB")


def eof(name=POE):
    candidate = orbit.parse_orbit(name)
    root = ET.Element("Earth_Explorer_File")
    header = ET.SubElement(ET.SubElement(root, "Earth_Explorer_Header"), "Fixed_Header")
    for key, value in {"File_Name": name[:-4], "File_Type": "AUX_" + candidate.kind,
                       "Mission": "Sentinel-1" + candidate.satellite[-1]}.items():
        ET.SubElement(header, key).text = value
    period = ET.SubElement(header, "Validity_Period")
    ET.SubElement(period, "Validity_Start").text = "UTC=" + candidate.start.isoformat()
    ET.SubElement(period, "Validity_Stop").text = "UTC=" + candidate.stop.isoformat()
    variable = ET.SubElement(root.find("Earth_Explorer_Header"), "Variable_Header")
    ET.SubElement(variable, "Ref_Frame").text = "EARTH_FIXED"
    ET.SubElement(variable, "Time_Reference").text = "UTC"
    vectors = ET.SubElement(ET.SubElement(root, "Data_Block"), "List_of_OSVs")
    for seconds in range(0, int((candidate.stop - candidate.start).total_seconds()) + 1, 10):
        vector = ET.SubElement(vectors, "OSV")
        ET.SubElement(vector, "UTC").text = "UTC=" + (candidate.start + timedelta(seconds=seconds)).isoformat()
        for key in ("X", "Y", "Z", "VX", "VY", "VZ"):
            ET.SubElement(vector, key, unit="m/s" if key.startswith("V") else "m").text = "1.5"
        ET.SubElement(vector, "Quality").text = "NOMINAL"
    vectors.set("count", str(len(vectors)))
    return ET.tostring(root)


class Response:
    def __init__(self, status=200, data=b"", location=None, chunks=None):
        self.status_code = status
        self.headers = {"Location": location} if location is not None else {}
        self.data, self.chunks = data, chunks

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def iter_content(self, chunk_size):
        for chunk in self.chunks if self.chunks is not None else [self.data]:
            if isinstance(chunk, BaseException): raise chunk
            yield chunk


def redirect(name=POE):
    return Response(302, location=orbit.parse_orbit(name).url)


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = next(self.responses)
        if isinstance(item, BaseException): raise item
        return item


class OrbitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.work = self.base / "work"
        with redirect_stdout(io.StringIO()): initialize(self.work)
        self.config = self.base / "project.yaml"
        self.config.write_text("work_dir: work\nschema_version: 1\n")
        self.slcs = self.work / "input/slc"
        (self.slcs / (SCENE + ".zip")).write_bytes(b"filename-only scan")
        self.output = self.work / "input/orbit"
        self.session = Session()
        self.mock_session = patch.object(orbit.requests, "Session", side_effect=lambda: self.session)
        self.mock_session.start()
        self.addCleanup(self.mock_session.stop)

    def run_app(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(["orbit", str(self.config), *args])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return result

    def test_download_precise_validates_xml_and_records_mapping(self):
        data = eof()
        self.session = Session(redirect(), Response(data=data))
        self.assertEqual(self.run_app(), 0, self.stderr)
        self.assertEqual((self.output / POE).read_bytes(), data)
        self.assertFalse(list(self.output.glob("*.part")))
        record = json.loads(next((self.work / "logs/orbits").glob("*.json")).read_text())
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["orbits"][0]["scenes"], [SCENE])
        self.assertEqual(record["orbits"][0]["type"], "POEORB")
        self.assertEqual(len(record["orbits"][0]["sha256"]), 64)
        self.assertTrue(all(not kwargs["allow_redirects"] for _, kwargs in self.session.calls))

    def test_dry_run_queries_only_and_creates_no_files(self):
        self.session = Session(redirect())
        before = set(self.base.rglob("*"))
        self.assertEqual(self.run_app("--dry-run", "--out", str(self.base / "new orbits")), 0)
        self.assertEqual(set(self.base.rglob("*")), before)
        self.assertEqual(len(self.session.calls), 1)

    def test_restituted_requires_opt_in(self):
        self.session = Session(redirect(RES))
        self.assertEqual(self.run_app(), 1)
        self.assertIn("--allow-restituted", self.stderr)
        self.assertFalse(list(self.output.glob("*.EOF")))
        self.assertEqual(len(self.session.calls), 1)

    def test_restituted_opt_in_downloads_and_records_type(self):
        self.session = Session(redirect(RES), Response(data=eof(RES)))
        self.assertEqual(self.run_app("--allow-restituted"), 0, self.stderr)
        record = json.loads(next((self.work / "logs/orbits").glob("*.json")).read_text())
        self.assertEqual(record["orbits"][0]["type"], "RESORB")

    def test_existing_precise_reused_without_network(self):
        (self.output / POE).write_bytes(eof())
        self.assertEqual(self.run_app(), 0, self.stderr)
        self.assertEqual(self.session.calls, [])

    def test_existing_restituted_can_upgrade_to_precise(self):
        old = eof(RES)
        (self.output / RES).write_bytes(old)
        self.session = Session(redirect(), Response(data=eof()))
        self.assertEqual(self.run_app("--allow-restituted"), 0, self.stderr)
        self.assertTrue((self.output / POE).exists())
        self.assertEqual((self.output / RES).read_bytes(), old)

    def test_corrupt_existing_file_is_preserved_and_stops(self):
        (self.output / POE).write_bytes(b"<html>error</html>")
        self.assertEqual(self.run_app(), 1)
        self.assertEqual((self.output / POE).read_bytes(), b"<html>error</html>")
        self.assertEqual(self.session.calls, [])

    def test_invalid_download_never_becomes_eof(self):
        for data in (b"<html>error</html>", eof().replace(b"Sentinel-1D", b"Sentinel-1A"),
                     eof()[:-40], eof().replace(b"NOMINAL", b"DEGRADED"),
                     eof().replace(b">1.5<", b">NaN<"),
                     eof().replace(b"EARTH_FIXED", b"INERTIAL"),
                     eof().replace(b'count="241"', b'count="242"')):
            with self.subTest(data=data[:40]):
                self.session = Session(redirect(), Response(data=data))
                self.assertEqual(self.run_app(), 1, self.stderr)
                self.assertFalse((self.output / POE).exists())

    def test_missing_orbit_does_not_download_any_eof(self):
        second = SCENE.replace("112235_", "112240_")
        (self.slcs / (second + ".zip")).write_bytes(b"slc")
        self.session = Session(redirect(), Response(404))
        self.assertEqual(self.run_app(), 1)
        self.assertFalse(list(self.output.glob("*.EOF")))
        self.assertEqual(len(self.session.calls), 2)

    def test_shared_orbit_downloaded_once(self):
        second = SCENE.replace("112235_", "112240_")
        (self.slcs / (second + ".zip")).write_bytes(b"slc")
        self.session = Session(redirect(), redirect(), Response(data=eof()))
        self.assertEqual(self.run_app(), 0, self.stderr)
        self.assertEqual(len(self.session.calls), 3)
        self.assertEqual(len(list(self.output.glob("*.EOF"))), 1)

    def test_discovery_handles_safe_duplicate_and_partial(self):
        (self.slcs / (SCENE + ".SAFE")).mkdir()
        (self.slcs / "other.zip.part").write_bytes(b"partial")
        with redirect_stdout(io.StringIO()): scenes = orbit.discover(self.slcs)
        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].satellite, "S1D")

    def test_no_completed_slcs_is_an_error(self):
        (self.slcs / (SCENE + ".zip")).rename(self.slcs / (SCENE + ".zip.part"))
        self.assertEqual(self.run_app("--dry-run"), 2)
        self.assertEqual(self.session.calls, [])

    def test_invalid_slc_name_is_an_error(self):
        (self.slcs / "wrong.zip").write_bytes(b"bad")
        self.assertEqual(self.run_app("--dry-run"), 2)

    def test_full_interval_and_margin_required(self):
        scene = orbit.discover(self.slcs)[0]
        self.assertTrue(orbit.parse_orbit(POE).covers(scene))
        self.assertFalse(orbit.parse_orbit(POE.replace("114000.EOF", "112320.EOF")).covers(scene))
        self.assertFalse(orbit.parse_orbit(POE.replace("V20260903T110000", "V20260903T112200")).covers(scene))
        self.assertFalse(orbit.parse_orbit(POE.replace("S1D", "S1A")).covers(scene))

    def test_untrusted_redirect_and_mismatched_satellite_rejected(self):
        for url in ("https://evil.example/" + POE, orbit.parse_orbit(POE).url + "?token=secret",
                    orbit.parse_orbit(POE.replace("S1D", "S1A")).url):
            self.session = Session(Response(302, location=url))
            self.assertEqual(self.run_app(), 1)
            self.assertNotIn("secret", self.stderr)
            self.assertFalse(list(self.output.glob("*.EOF")))

    def test_symlink_orbit_target_rejected_without_changing_other_file(self):
        target = self.base / "keep"
        target.write_bytes(eof())
        (self.output / POE).symlink_to(target)
        self.assertEqual(self.run_app(), 1)
        self.assertEqual(target.read_bytes(), eof())

    def test_interrupted_transfer_preserves_partial_not_final(self):
        self.session = Session(redirect(), Response(chunks=[b"partial", KeyboardInterrupt()]))
        self.assertEqual(self.run_app(), 130)
        self.assertFalse((self.output / POE).exists())
        self.assertEqual((self.output / (POE + ".part")).read_bytes(), b"partial")

    def test_dns_retry_during_lookup(self):
        self.session = Session(requests.ConnectionError(socket.gaierror(-5, "DNS")), redirect())
        with patch("sentinel_1_stack.slc.time.sleep"):
            self.assertEqual(self.run_app("--dry-run"), 0)
        self.assertIn("DNS 再試行", self.stdout)

    def test_custom_paths_used(self):
        (self.work / "input/slc").rename(self.work / "input/scenes")
        (self.work / "input/orbit").rename(self.work / "input/orbits")
        self.config.write_text("work_dir: work\nschema_version: 1\npaths:\n  slc: input/scenes\n  orbit: input/orbits\n")
        self.session = Session(redirect(), Response(data=eof()))
        self.assertEqual(self.run_app(), 0, self.stderr)
        self.assertTrue((self.work / "input/orbits" / POE).exists())

    def test_xml_with_missing_state_vectors_in_acquisition_window_is_rejected(self):
        root = ET.fromstring(eof())
        vectors = root.find("Data_Block/List_of_OSVs")
        for vector in list(vectors):
            timestamp = vector.findtext("UTC")
            if "T11:22:" in timestamp or "T11:23:" in timestamp:
                vectors.remove(vector)
        vectors.set("count", str(len(vectors)))
        self.session = Session(redirect(), Response(data=ET.tostring(root)))
        self.assertEqual(self.run_app(), 1)
        self.assertFalse((self.output / POE).exists())
