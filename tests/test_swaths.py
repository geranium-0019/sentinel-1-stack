from pathlib import Path
import tempfile
import unittest
import zipfile

from sentinel_1_stack.swaths import select
from sentinel_1_stack.workspace import WorkspaceError


def annotation(n, west, east):
    points = ''.join(f'<geolocationGridPoint><line>{line}</line><pixel>{pixel}</pixel>'
                     f'<longitude>{x}</longitude><latitude>{y}</latitude></geolocationGridPoint>'
                     for line, y in [(0, 0), (1, 2)] for pixel, x in [(0, west), (1, east)])
    return f'<product><adsHeader><swath>IW{n}</swath></adsHeader><geolocationGrid>{points}</geolocationGrid></product>'


class SwathTests(unittest.TestCase):
    def fixture(self, root, date='20170921', safe=False, shift=0, missing=False):
        name = f'S1A_IW_SLC__1SDV_{date}T000000_TEST'
        files = {f'annotation/s1a-iw{n}-slc-vv-test.xml': annotation(n, n-1+shift, n+shift)
                 for n in (1, 2, 3) if not (missing and n == 2)}
        if safe:
            path = root / (name + '.SAFE')
            (path / 'annotation').mkdir(parents=True)
            for file, data in files.items():
                (path / file).write_text(data)
        else:
            path = root / (name + '.zip')
            with zipfile.ZipFile(path, 'w') as z:
                for file, data in files.items():
                    z.writestr(name + '.SAFE/' + file, data)
        return path

    def test_zip_safe_and_multiple_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [self.fixture(root), self.fixture(root, '20171003', safe=True)]
            selected, evidence = select(paths, [0.5, 1.5, 2.2, 2.8], 'vv')
            self.assertEqual(selected, [3])
            self.assertEqual(len(evidence), 2)
            self.assertEqual(select(paths, [0.5, 1.5, 1.5, 2.5], 'vv')[0], [2, 3])
            # Touching an adjacent swath at the boundary does not select it.
            self.assertEqual(select(paths, [0.5, 1.5, 2, 2.5], 'vv')[0], [3])

    def test_rejects_no_overlap_partial_coverage_and_wrong_polarization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.fixture(Path(tmp))
            for bbox, pol in [([0.5, 1.5, 4, 5], 'vv'), ([0.5, 2.5, 2.2, 2.8], 'vv'),
                              ([0.5, 1.5, 2.2, 2.8], 'vh')]:
                with self.subTest(bbox=bbox, pol=pol), self.assertRaises(WorkspaceError):
                    select([path], bbox, pol)

    def test_rejects_missing_annotations_and_changing_swaths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = self.fixture(root, missing=True)
            with self.assertRaises(WorkspaceError):
                select([bad], [0.5, 1.5, 2.2, 2.8], 'vv')
            good = self.fixture(root)
            shifted = self.fixture(root, '20171003', shift=0.5)
            with self.assertRaisesRegex(WorkspaceError, '観測日ごと'):
                select([good, shifted], [0.5, 1.5, 2.2, 2.8], 'vv')
