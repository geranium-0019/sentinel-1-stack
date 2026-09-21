"""Quality report and common-grid, circularly resampled wrapped phase GeoTIFFs."""
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
import fcntl
from contextlib import redirect_stdout, redirect_stderr, contextmanager

import numpy as np
import yaml

from . import config
from .runner import execution_plan
from .slc import Tee
from .workspace import WorkspaceError


def add_arguments(parser):
    parser.add_argument('config', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--include-unwrapped', action='store_true', help='完了済みアンラップ位相と連結成分も出力')


def circular_components(raw, filtered, coherence):
    valid = (np.isfinite(raw) & np.isfinite(filtered) & (abs(raw) > 0) & (abs(filtered) > 0)
             & np.isfinite(coherence) & (coherence >= 0) & (coherence <= 1))
    bands = []
    for z in (filtered, raw):
        unit = np.divide(z, abs(z), out=np.zeros_like(z), where=abs(z) > 0)
        bands.extend([np.where(valid, unit.real, np.nan), np.where(valid, unit.imag, np.nan)])
    bands.append(np.where(valid, coherence, np.nan))
    return np.asarray(bands, dtype=np.float32)


def reconstruct(bands):
    valid = np.all(np.isfinite(bands), axis=0)
    valid &= (np.hypot(bands[0], bands[1]) > 1e-6) & (np.hypot(bands[2], bands[3]) > 1e-6)
    return [np.where(valid, np.arctan2(bands[1], bands[0]), np.nan),
            np.where(valid, np.arctan2(bands[3], bands[2]), np.nan),
            np.where(valid, np.clip(bands[4], 0, 1), np.nan)]


def raster(gdal, path, width, height, bands):
    ds = gdal.GetDriverByName('GTiff').Create(str(path), width, height, bands, gdal.GDT_Float32,
            options=['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=3', 'BIGTIFF=IF_SAFER'])
    for b in range(1, bands+1):
        ds.GetRasterBand(b).SetNoDataValue(float('nan'))
    return ds


@contextmanager
def geocode_temporary(gdal, parent):
    previous = gdal.GetConfigOption('CPL_TMPDIR')
    with tempfile.TemporaryDirectory(prefix='.geocode-', dir=parent) as tmp:
        gdal.SetConfigOption('CPL_TMPDIR', tmp)
        try:
            yield tmp
        finally:
            gdal.SetConfigOption('CPL_TMPDIR', previous)


def export_pair(source, geometry, destination, bbox, pixel_size, resampling):
    from osgeo import gdal, osr
    gdal.UseExceptions()
    gdal.SetCacheMax(256 * 1024 * 1024)
    raw = gdal.Open(str(source/'fine.int.vrt'))
    filtered = gdal.Open(str(source/'filt_fine.int.vrt'))
    coherence = gdal.Open(str(source/'filt_fine.cor.vrt'))
    width, height = raw.RasterXSize, raw.RasterYSize
    for path in ('lat.rdr.vrt', 'lon.rdr.vrt'):
        ds = gdal.Open(str(geometry/path))
        if (ds.RasterXSize, ds.RasterYSize) != (width, height):
            raise WorkspaceError('緯度経度と干渉画像の寸法が異なります。自動で引き伸ばさず停止します。')
    if any((d.RasterXSize, d.RasterYSize) != (width, height) for d in (filtered, coherence)):
        raise WorkspaceError('位相・コヒーレンスの寸法が異なります。')
    south, north, west, east = bbox
    nx, ny = round((east-west)/pixel_size), round((north-south)/pixel_size)
    if nx < 1 or ny < 1 or nx*ny > 100_000_000:
        raise WorkspaceError('出力グリッドの画素数が範囲外です。')
    reference = osr.SpatialReference()
    reference.ImportFromEPSG(4326)
    with geocode_temporary(gdal, destination.parent) as tmp:
        temporary = Path(tmp)
        unit = raster(gdal, temporary/'circular.tif', width, height, 5)
        for y in range(0, height, 128):
            count = min(128, height-y)
            arrays = [d.ReadAsArray(0, y, width, count) for d in (raw, filtered, coherence)]
            for b, data in enumerate(circular_components(*arrays), 1):
                unit.GetRasterBand(b).WriteArray(data, 0, y)
        unit.SetMetadata({'SRS': reference.ExportToWkt(), 'X_DATASET': str(geometry/'lon.rdr.vrt'),
            'Y_DATASET': str(geometry/'lat.rdr.vrt'), 'X_BAND': '1', 'Y_BAND': '1',
            'PIXEL_OFFSET': '0', 'LINE_OFFSET': '0', 'PIXEL_STEP': '1', 'LINE_STEP': '1',
            'GEOREFERENCING_CONVENTION': 'PIXEL_CENTER'}, 'GEOLOCATION')
        unit.FlushCache()
        warped = gdal.Warp(str(temporary/'warped.tif'), unit, format='GTiff',
            dstSRS='EPSG:4326', geoloc=True, outputBounds=[west, south, east, north],
            width=nx, height=ny, resampleAlg=resampling, srcNodata=float('nan'), dstNodata=float('nan'),
            warpMemoryLimit=256, errorThreshold=0, creationOptions=['TILED=YES', 'COMPRESS=DEFLATE'],
            warpOptions=['UNIFIED_SRC_NODATA=YES'])
        if warped is None:
            raise WorkspaceError('GDALによるジオコードが失敗しました。')
        outputs = []
        names = ('phase_final.tif', 'phase_raw.tif', 'coherence.tif')
        for name in names:
            ds = raster(gdal, temporary/name, nx, ny, 1)
            ds.SetGeoTransform(warped.GetGeoTransform())
            ds.SetProjection(warped.GetProjection())
            ds.GetRasterBand(1).SetUnitType('radian' if name.startswith('phase') else '1')
            ds.SetMetadata({'PAIR': source.name, 'UNITS': 'radian' if name.startswith('phase') else '1',
                'RESAMPLING': 'unit_phasor_' + resampling, 'PHASE_SIGN': 'arg(earlier * conjugate(later)); ISCE2 native'})
            outputs.append(ds)
        values = []
        valid_count = 0
        for y in range(0, ny, 128):
            rows = reconstruct(warped.ReadAsArray(0, y, nx, min(128, ny-y)))
            for ds, array in zip(outputs, rows):
                ds.GetRasterBand(1).WriteArray(array, 0, y)
            c = rows[2][np.isfinite(rows[2])]
            valid_count += len(c)
            values.append(c)
        if not valid_count:
            raise WorkspaceError('出力範囲に有効な位相・コヒーレンスがありません。')
        for ds in outputs:
            ds.FlushCache()
        outputs.clear()
        ds = None
        warped = None
        unit = None
        destination.mkdir()
        for name in names:
            shutil.move(str(temporary/name), destination/name)
        c = np.concatenate(values)
        result = {'pair': source.name, 'valid_pixels': valid_count, 'total_pixels': nx*ny,
            'coherence_percentiles': dict(zip(('p10', 'p50', 'p90'), map(float, np.percentile(c, [10,50,90])))),
            'fraction_coherence_ge_0.3': float(np.mean(c>=0.3)), 'fraction_coherence_ge_0.7': float(np.mean(c>=0.7)),
            'grid': {'epsg': 4326, 'width': nx, 'height': ny, 'bbox_snwe': bbox,
                     'pixel_size_degrees': [(east-west)/nx, (north-south)/ny]},
            'note': '海域・陸域を分けない範囲内の統計。低コヒーレンスは削除していません。'}
        preview(destination, bbox, result)
        return result


def preview(directory, bbox, result):
    from osgeo import gdal
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.5), constrained_layout=True)
    for ax, name, title in zip(axes, ('phase_raw.tif', 'phase_final.tif', 'coherence.tif'),
                               ('Raw wrapped phase [rad]', 'Filtered wrapped phase [rad]', 'Coherence')):
        ds = gdal.Open(str(directory/name))
        a = ds.ReadAsArray(buf_xsize=min(ds.RasterXSize,800), buf_ysize=min(ds.RasterYSize,800))
        is_phase = name.startswith('phase')
        im = ax.imshow(a, origin='upper', extent=[bbox[2],bbox[3],bbox[0],bbox[1]],
                       cmap='twilight' if is_phase else 'viridis',
                       vmin=-np.pi if is_phase else 0, vmax=np.pi if is_phase else 1)
        ax.set_title(title)
        ax.set_xlabel('Longitude')
        from matplotlib.ticker import MaxNLocator, FormatStrFormatter
        ax.xaxis.set_major_locator(MaxNLocator(4))
        ax.xaxis.set_major_formatter(FormatStrFormatter('%.2f'))
        fig.colorbar(im, ax=ax, shrink=0.7)
    axes[0].set_ylabel('Latitude')
    fig.suptitle(directory.name)
    fig.savefig(directory/'preview.png', dpi=130)
    plt.close(fig)


def nesd_report(directory, direction='azimuth'):
    result = []
    for path in sorted((directory/'misreg'/direction/'pairs').glob('*/*.txt')):
        values = {}
        for line in path.read_text().splitlines():
            if ':' in line:
                k, v = line.split(':',1)
                values[k.strip()] = float(v.strip())
        if not all(math.isfinite(v) for v in values.values()):
            raise WorkspaceError(f'NESD推定値に非有限値があります: {path}')
        result.append({'pair': path.stem, **values})
    return result


def run(args):
    try:
        directory, logs, fingerprint, steps, _ = execution_plan(args.config)
        state = json.loads((logs/'run.json').read_text())
        if state['fingerprint'] != fingerprint or state['steps'].get('run_15_filter_coherence',{}).get('status') != 'complete':
            raise WorkspaceError('現在の設定で filter_coherence まで完了していません。')
        include_unwrapped = getattr(args, 'include_unwrapped', False)
        if include_unwrapped:
            from .unwrapped_export import require_unwrap_complete, export_unwrapped
            require_unwrap_complete(state, steps)
        settings, root = config.load(args.config)
        document = yaml.safe_load(args.config.read_text())
        options = document.get('export', {})
        if not isinstance(options, dict) or set(options)-{'pixel_size_degrees','resampling'}:
            raise WorkspaceError('export には pixel_size_degrees と resampling を指定してください。')
        resolution = options.get('pixel_size_degrees',0.0001)
        method = options.get('resampling','bilinear')
        if type(resolution) not in (float,int) or not math.isfinite(resolution) or resolution<=0 or method not in ('bilinear','near'):
            raise WorkspaceError('export の画素間隔は正の有限値、resampling は bilinear / near にしてください。')
        bbox = document['processing']['bbox']
        pairs = json.loads((logs/'prepare.json').read_text())['pairs']
        target = root/settings['paths']['output']/'geocoded'
        print(f'出力: {target}\n画素間隔（度）: {resolution}\nペア数: {len(pairs)}',flush=True)
        if args.dry_run:
            return 0
        with (logs/'.run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            if target.exists():
                raise WorkspaceError(f'既存出力は上書きしません: {target}')
            with (logs/'export.log').open('a') as stream, redirect_stdout(Tee(sys.stdout,stream)), redirect_stderr(Tee(sys.stderr,stream)):
                report = {'status':'running','options':options,'include_unwrapped':include_unwrapped,'pairs':[], 'nesd':nesd_report(directory), 'range_registration':nesd_report(directory, 'range')}
                try:
                    with tempfile.TemporaryDirectory(prefix='.export-',dir=target.parent) as tmp:
                        staging = Path(tmp)
                        for a,b in pairs:
                            name=a+'_'+b
                            print('ジオコード: '+name,flush=True)
                            report['pairs'].append(export_pair(directory/'merged/interferograms'/name,
                                directory/'merged/geom_reference',staging/name,bbox,resolution,method))
                            if include_unwrapped:
                                report['pairs'][-1]['unwrapped'] = export_unwrapped(directory/'merged/interferograms'/name,
                                    directory/'merged/geom_reference',staging/name)
                        body = '<meta charset="utf-8"><title>干渉画像の品質確認</title><h1>干渉画像の品質確認</h1>'
                        body += '<p>未フィルタ位相・フィルタ位相・コヒーレンス。海域を含む統計であり、精度保証ではありません。</p>'
                        body += '<p><a href="quality.json">NESD・位置合わせ・コヒーレンス統計（JSON）</a></p>'
                        for pair in report['pairs']:
                            body += f'<h2>{pair["pair"]}</h2><img style="max-width:100%" src="{pair["pair"]}/preview.png">'
                            if 'unwrapped' in pair:
                                body += (f'<p><a href="{pair["pair"]}/phase_unwrapped.tif">アンラップ位相（rad）</a> / '
                                         f'<a href="{pair["pair"]}/connected_components.tif">連結成分</a>。'
                                         '成分間の位相オフセットは未確定です。変位の基準点は設定していません。</p>')
                        (staging/'report.html').write_text(body)
                        report['status']='complete'
                        (staging/'quality.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
                        staging.rename(target)
                    print('GeoTIFFと品質確認レポートを作成しました。',flush=True)
                except BaseException as exc:
                    report.update(status='failed',error=str(exc))
                    raise
                finally:
                    (logs/'export.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 2
