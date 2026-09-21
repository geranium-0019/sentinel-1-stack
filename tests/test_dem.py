"""DEM footprint, download integrity, explicit fill, and atomic publication tests."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from sentinel_1_stack import dem
from sentinel_1_stack.cli import main
from sentinel_1_stack.workspace import initialize

NAME = "S1D_IW_SLC__1SDV_20260903T112235_20260903T112306_004412_0082B1_61FC"


def manifest(points="-6.8,105.2 -6.2,105.2 -6.2,105.8 -6.8,105.8"):
    return ('<root xmlns:s="urn:safe" xmlns:g="http://www.opengis.net/gml">'
            '<s:footPrint srsName="http://www.opengis.net/gml/srs/epsg.xml#4326">'
            f'<g:coordinates>{points}</g:coordinates></s:footPrint></root>').encode()


def zip_bytes(name="S07E105", heights=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name + ".hgt", struct.pack(">9h", *(heights or [10] * 9)))
    return buffer.getvalue()


class Response:
    def __init__(self, data=b"", status=200, chunks=None):
        self.data, self.status_code, self.chunks = data, status, chunks
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def iter_content(self, chunk_size):
        for chunk in self.chunks if self.chunks is not None else [self.data]:
            if isinstance(chunk, BaseException): raise chunk
            yield chunk


class Session:
    def __init__(self, *responses): self.responses, self.calls = iter(responses), []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def get(self, url, **kwargs):
        self.calls.append(url)
        return next(self.responses)


class DemTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.work = self.base / "work"
        with redirect_stdout(io.StringIO()): initialize(self.work)
        self.config = self.base / "project.yaml"
        self.config.write_text("work_dir: work\nschema_version: 1\n")
        self.slcs = self.work / "input/slc"
        self.write_scene()
        self.output = self.work / "input/dem"
        self.session = Session()
        for patcher in (patch.object(dem.requests, "Session", side_effect=lambda: self.session),
                        patch.object(dem, "SAMPLES", 3), patch.object(dem, "HGT_BYTES", 18)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write_scene(self, name=NAME, points=None):
        path = self.slcs / (name + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(name + ".SAFE/manifest.safe", manifest() if points is None else manifest(points))
            archive.writestr(name + ".SAFE/measurement/not-read.tiff", b"fixture")
        return path

    def run_app(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["dem", str(self.config), *args])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code

    @staticmethod
    def fake_stitch(staging, bounds):
        (staging / "dem.wgs84").write_bytes(b"elevation")
        (staging / "dem.wgs84.vrt").write_text('<VRTDataset><SourceFilename relativeToVRT="1">dem.wgs84</SourceFilename></VRTDataset>')
        (staging / "dem.wgs84.xml").write_text('<component><property name="FILE_NAME"><value>old</value></property></component>')

    def test_manifest_latitude_longitude_and_negative_rounding(self):
        self.assertEqual(dem.footprint(manifest()), [-6.8, -6.2, 105.2, 105.8])
        plan = dem.make_plan(self.slcs, 0.1)
        self.assertEqual(plan["bbox_snwe"], [-7, -6, 105, 106])
        self.assertEqual(plan["tiles"], ["S07E105"])
        self.assertEqual(len(plan["inputs"][0]["manifest_sha256"]), 64)

    def test_union_covers_all_images(self):
        self.write_scene(NAME.replace("20260903", "20260822"), "-7.2,104.5 -7.1,104.5 -7.1,104.9 -7.2,104.9")
        plan = dem.make_plan(self.slcs, 0)
        self.assertEqual(plan["bbox_snwe"], [-8, -6, 104, 106])
        self.assertEqual(len(plan["inputs"]), 2)

    def test_safe_manifest_and_zip_are_deduplicated(self):
        safe = self.slcs / (NAME + ".SAFE")
        safe.mkdir()
        (safe / "manifest.safe").write_bytes(manifest())
        self.assertEqual(len(dem.make_plan(self.slcs, 0.1)["inputs"]), 1)

    def test_partial_slc_does_not_expand_bounds(self):
        (self.slcs / "incomplete.zip.part").write_bytes(b"part")
        with redirect_stdout(io.StringIO()): plan = dem.make_plan(self.slcs, 0.1)
        self.assertEqual(plan["tiles"], ["S07E105"])

    def test_bad_coordinates_crs_and_unsafe_xml_rejected(self):
        documents = [b"<html/>", b"<bad", manifest("nan,105 -6,105 -6,106 -7,106"),
                     manifest("105,-6 105,-7 106,-7 106,-6"),
                     manifest().replace(b"#4326", b"#3857"),
                     b'<!DOCTYPE root [<!ENTITY x "text">]><root/>']
        for data in documents:
            with self.subTest(data=data):
                with self.assertRaises(dem.InputError): dem.footprint(data)

    def test_antimeridian_and_unsupported_latitude_fail(self):
        for points in ("10,179 11,179 11,-179 10,-179", "65,20 66,20 66,21 65,21"):
            self.write_scene(points=points)
            self.assertEqual(self.run_app("--dry-run"), 2)
        self.assertEqual(self.session.calls, [])

    def test_margin_is_validated(self):
        for value in ("-1", "2", "nan"):
            self.assertEqual(self.run_app("--margin", value, "--dry-run"), 2)
        self.assertEqual(self.session.calls, [])

    def test_missing_or_duplicate_zip_manifest_rejected(self):
        path = self.slcs / (NAME + ".zip")
        with zipfile.ZipFile(path, "w") as archive: archive.writestr("wrong/manifest.safe", manifest())
        self.assertEqual(self.run_app("--dry-run"), 2)
        self.assertEqual(self.session.calls, [])

    def test_dry_run_only_reads_catalog_and_writes_nothing(self):
        self.session = Session(Response(b'<a href="S07E105.SRTMGL1.hgt.zip">tile</a>'))
        before = set(self.base.rglob("*"))
        self.assertEqual(self.run_app("--dry-run"), 0, self.stderr)
        self.assertEqual(before, set(self.base.rglob("*")))
        self.assertEqual(self.session.calls, [dem.SOURCE])

    def test_missing_tiles_require_explicit_fill_with_generic_example(self):
        self.session = Session(Response(b"N01E001.SRTMGL1.hgt.zip"))
        before = set(self.base.rglob("*"))
        self.assertEqual(self.run_app("--dry-run"), 1)
        self.assertIn("scripts/run.sh dem config/project.yaml --fill-missing-zero", self.stderr)
        self.assertIn("S07E105", self.stdout)
        self.assertEqual(before, set(self.base.rglob("*")))

    def test_fill_dry_run_creates_no_synthetic_files(self):
        self.session = Session(Response(b"N01E001.SRTMGL1.hgt.zip"))
        before = set(self.base.rglob("*"))
        self.assertEqual(self.run_app("--dry-run", "--fill-missing-zero"), 0)
        self.assertEqual(before, set(self.base.rglob("*")))

    def test_http_failure_never_becomes_zero_tile(self):
        self.session = Session(Response(b"S07E105.SRTMGL1.hgt.zip"), Response(status=503))
        self.assertEqual(self.run_app("--fill-missing-zero"), 1)
        self.assertIn("HTTP 503", self.stderr)
        self.assertFalse((self.output / "srtm1_S07E105_S06E106").exists())

    def test_bad_download_never_becomes_cached_tile(self):
        self.session = Session(Response(b"<html>not ZIP</html>"))
        with self.assertRaises(dem.DownloadError): dem.fetch_tile(self.session, self.output, "S07E105")
        self.assertFalse((self.output / "S07E105.SRTMGL1.hgt.zip").exists())

    def test_existing_bad_cache_is_not_overwritten(self):
        path = self.output / "S07E105.SRTMGL1.hgt.zip"
        path.write_bytes(b"bad cache")
        with self.assertRaises(dem.DownloadError): dem.fetch_tile(self.session, self.output, "S07E105")
        self.assertEqual(path.read_bytes(), b"bad cache")
        self.assertEqual(self.session.calls, [])

    def test_zip_traversal_and_wrong_size_rejected(self):
        path = self.output / "bad.zip"
        for member, data in (("../S07E105.hgt", bytes(18)), ("S07E105.hgt", bytes(2))):
            with zipfile.ZipFile(path, "w") as z: z.writestr(member, data)
            with self.assertRaises(dem.DownloadError): dem.validate_tile(path, "S07E105")

    def test_void_pixels_require_explicit_fill_and_original_cache_is_preserved(self):
        original = zip_bytes(heights=[-32768] + [10] * 8)
        path = self.output / "S07E105.SRTMGL1.hgt.zip"
        path.write_bytes(original)
        staging = self.base / "stage"
        staging.mkdir()
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(dem.DownloadError):
                dem.stage_tiles(self.session, ["S07E105"], [], self.output, staging, False)
            records = dem.stage_tiles(self.session, ["S07E105"], [], self.output, staging, True)
        self.assertEqual(records[0]["filled_pixels"], 1)
        data, voids = dem.validate_tile(staging / path.name, "S07E105")
        self.assertEqual(voids, 0)
        self.assertEqual(struct.unpack(">9h", data)[0], 0)
        self.assertEqual(path.read_bytes(), original)

    def test_publish_reuse_and_tamper_detection(self):
        self.session = Session(Response(b"S07E105.SRTMGL1.hgt.zip"), Response(zip_bytes()))
        config_before = self.config.read_bytes()
        with patch.object(dem, "run_dem_py", side_effect=self.fake_stitch), patch.object(dem, "validate_dem"):
            self.assertEqual(self.run_app(), 0, self.stderr)
            final = self.output / "srtm1_S07E105_S06E106"
            record = json.loads((final / "dem.json").read_text())
            self.assertEqual(record["vertical_reference"], "WGS84")
            self.assertIn(str(final / "dem.wgs84"), (final / "dem.wgs84.xml").read_text())
            self.assertEqual(self.config.read_bytes(), config_before)
            before = {p.name: p.stat().st_mtime_ns for p in final.iterdir()}
            self.session = Session()
            self.assertEqual(self.run_app(), 0)
            self.assertEqual(self.session.calls, [])
            self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in final.iterdir()})
            (final / "dem.wgs84").write_bytes(b"tampered")
            self.assertEqual(self.run_app(), 1)
            self.assertEqual((final / "dem.wgs84").read_bytes(), b"tampered")

    def test_dem_process_failure_does_not_publish_bundle(self):
        self.session = Session(Response(b"S07E105.SRTMGL1.hgt.zip"), Response(zip_bytes()))
        with patch.object(dem, "run_dem_py", side_effect=dem.DownloadError("failed subprocess")):
            self.assertEqual(self.run_app(), 1)
        self.assertFalse((self.output / "srtm1_S07E105_S06E106").exists())
        self.assertFalse(list(self.output.glob(".dem-build-*")))
        record = json.loads(next((self.work / "logs/dem").glob("*.json")).read_text())
        self.assertEqual(record["status"], "failed")

    def test_filled_bundle_reuse_requires_same_explicit_opt_in(self):
        self.session = Session(Response(b"N01E001.SRTMGL1.hgt.zip"))
        with patch.object(dem, "run_dem_py", side_effect=self.fake_stitch), patch.object(dem, "validate_dem"):
            self.assertEqual(self.run_app("--fill-missing-zero"), 0, self.stderr)
            self.session = Session()
            self.assertEqual(self.run_app(), 1)
            self.assertEqual(self.session.calls, [])
            self.assertIn("--fill-missing-zero", self.stderr)

    def test_interrupted_transfer_leaves_only_cache_partial(self):
        self.session = Session(Response(b"S07E105.SRTMGL1.hgt.zip"), Response(chunks=[b"partial", KeyboardInterrupt()]))
        self.assertEqual(self.run_app(), 130)
        self.assertFalse((self.output / "srtm1_S07E105_S06E106").exists())
        self.assertTrue(list((self.output / "tiles").glob("*.part")))
