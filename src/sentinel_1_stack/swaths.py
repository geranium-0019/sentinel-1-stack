"""Select IW swaths from annotation geolocation footprints without reading SLC pixels."""
from collections import defaultdict
from pathlib import Path
import math
import xml.etree.ElementTree as ET
import zipfile

from .workspace import WorkspaceError


def footprint(data):
    from osgeo import ogr
    root = ET.fromstring(data)
    rows = defaultdict(list)
    for point in root.findall('.//geolocationGridPoint'):
        x, y = float(point.findtext('longitude')), float(point.findtext('latitude'))
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
            raise ValueError('不正な経緯度')
        rows[int(point.findtext('line'))].append((int(point.findtext('pixel')), (x, y)))
    grid = [sorted(rows[k]) for k in sorted(rows)]
    if len(grid) < 2 or any(len(row) < 2 for row in grid):
        raise ValueError('位置情報グリッドが不足')
    boundary = ([v[1] for v in grid[0]] + [r[-1][1] for r in grid[1:]] +
                [v[1] for v in reversed(grid[-1][:-1])] + [r[0][1] for r in reversed(grid[1:-1])])
    if max(x for x, y in boundary) - min(x for x, y in boundary) > 180:
        raise ValueError('日付変更線をまたぐ画像の自動判定は未対応')
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in boundary + boundary[:1]:
        ring.AddPoint_2D(x, y)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    if not poly.IsValid() or poly.GetArea() <= 0:
        raise ValueError('不正な画像外周')
    return root.findtext('adsHeader/swath'), poly


def annotations(path, polarization):
    path = Path(path)
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                parts = Path(name).parts
                if len(parts) >= 2 and parts[-2] == 'annotation' and name.endswith('.xml') and f'-{polarization}-' in parts[-1]:
                    yield archive.read(name)
    else:
        for file in sorted((path / 'annotation').glob(f'*-{polarization}-*.xml')):
            yield file.read_bytes()


def select(inputs, bbox, polarization):
    from osgeo import ogr
    south, north, west, east = bbox
    target = ogr.CreateGeometryFromWkt(
        f'POLYGON (({west} {south},{east} {south},{east} {north},{west} {north},{west} {south}))')
    by_date = defaultdict(dict)
    for path in inputs:
        date = Path(path).name[17:25]
        found = set()
        try:
            for data in annotations(path, polarization):
                name, polygon = footprint(data)
                if name not in ('IW1', 'IW2', 'IW3'):
                    raise ValueError('IWサブスワス名が不正')
                number = int(name[-1])
                found.add(number)
                previous = by_date[date].get(number)
                by_date[date][number] = polygon if previous is None else previous.Union(polygon)
            if found != {1, 2, 3}:
                raise ValueError('IW1・IW2・IW3のannotationが揃っていません')
        except (OSError, ValueError, TypeError, ET.ParseError, zipfile.BadZipFile) as exc:
            raise WorkspaceError(f'swathを自動判定できません: {path}: {exc}。SLCを確認してください。') from exc
    selected = set()
    evidence = []
    for date, polygons in sorted(by_date.items()):
        overlaps = [n for n, poly in sorted(polygons.items()) if poly.Intersection(target).GetArea() > 1e-12]
        if not overlaps:
            raise WorkspaceError(f'{date}: bboxとSLCが重なりません。bbox・入力SLCを確認してください。')
        covered = polygons[overlaps[0]].Clone()
        for n in overlaps[1:]:
            covered = covered.Union(polygons[n])
        fraction = covered.Intersection(target).GetArea() / target.GetArea()
        if fraction < 1 - 1e-6:
            raise WorkspaceError(f'{date}: SLCがbbox全体を覆っていません（約{fraction:.1%}）。bboxを狭めるか不足するSLCを追加してください。')
        selected.update(overlaps)
        evidence.append({'date': date, 'swaths': overlaps, 'bbox_coverage': min(fraction, 1.0)})
    if not selected:
        raise WorkspaceError('swath自動判定に使えるSLCがありません。')
    if any(set(item['swaths']) != selected for item in evidence):
        raise WorkspaceError('観測日ごとにbboxと重なるswathが異なります。bboxを調整するか、共通のswathsを明示指定してください。')
    return sorted(selected), evidence
