"""Geocode ISCE2 SNAPHU phase and component labels without mixing components."""
from pathlib import Path
import numpy as np
from .workspace import WorkspaceError


def require_unwrap_complete(state, steps):
    names = [name for name, _ in steps if name.endswith('_unwrap')]
    if not names or any(state.get('steps', {}).get(name, {}).get('status') != 'complete' for name in names):
        raise WorkspaceError('アンラップの完了記録がありません。processing.unwrap: true で run --resume を完了してください。手動実行だけでは完了扱いにしません。')


def export_unwrapped(source, geometry, destination):
    from osgeo import gdal
    from .export import geocode_temporary, raster
    gdal.UseExceptions()
    phase_path = source/'filt_fine.unw'
    component_path = source/'filt_fine.unw.conncomp'
    for path in (phase_path, component_path):
        if not path.is_file() or not Path(str(path)+'.vrt').is_file():
            raise WorkspaceError(f'アンラップ位相・連結成分のファイルとVRTが必要です: {path}')
    unw = gdal.Open(str(phase_path)+'.vrt')
    labels = gdal.Open(str(component_path)+'.vrt')
    width, height = unw.RasterXSize, unw.RasterYSize
    if unw.RasterCount != 2 or labels.RasterCount != 1:
        raise WorkspaceError('ISCE2の2バンドunw（振幅・位相）と1バンドconncompが必要です。')
    if (labels.RasterXSize, labels.RasterYSize) != (width, height):
        raise WorkspaceError('アンラップ位相と連結成分の寸法が異なります。')
    # ISCE2 writes two float32 BIL bands and byte component labels, without a header.
    if phase_path.stat().st_size != width*height*8 or component_path.stat().st_size != width*height:
        raise WorkspaceError('アンラップ出力のファイルサイズが不正です。途中終了・破損の可能性があります。')
    if unw.GetRasterBand(2).DataType != gdal.GDT_Float32 or labels.GetRasterBand(1).DataType != gdal.GDT_Byte:
        raise WorkspaceError('ISCE2のFloat32位相・Byte連結成分が必要です。')
    for name in ('lat.rdr.vrt', 'lon.rdr.vrt'):
        ds = gdal.Open(str(geometry/name))
        if (ds.RasterXSize, ds.RasterYSize) != (width, height):
            raise WorkspaceError('アンラップ出力と緯度経度の寸法が異なります。')
    reference = gdal.Open(str(destination/'phase_final.tif'))
    gt = reference.GetGeoTransform()
    nx, ny = reference.RasterXSize, reference.RasterYSize
    outputs = [destination/'phase_unwrapped.tif', destination/'connected_components.tif']
    if any(p.exists() for p in outputs):
        raise WorkspaceError('既存のアンラップGeoTIFFは上書きしません。')
    with geocode_temporary(gdal, destination.parent) as tmp:
        tmp = Path(tmp)
        src = raster(gdal, tmp/'phase_labels.tif', width, height, 2)
        for y in range(0, height, 128):
            n = min(128, height-y)
            phase = unw.GetRasterBand(2).ReadAsArray(0,y,width,n)
            component = labels.ReadAsArray(0,y,width,n)
            valid = np.isfinite(phase) & (component > 0)
            src.GetRasterBand(1).WriteArray(np.where(valid,phase,np.nan),0,y)
            src.GetRasterBand(2).WriteArray(np.where(valid,component,np.nan),0,y)
        src.SetMetadata({'SRS': reference.GetProjection(), 'X_DATASET': str(geometry/'lon.rdr.vrt'),
            'Y_DATASET': str(geometry/'lat.rdr.vrt'), 'X_BAND':'1','Y_BAND':'1',
            'PIXEL_OFFSET':'0','LINE_OFFSET':'0','PIXEL_STEP':'1','LINE_STEP':'1',
            'GEOREFERENCING_CONVENTION':'PIXEL_CENTER'}, 'GEOLOCATION')
        src.FlushCache()
        warped = gdal.Warp(str(tmp/'warped.tif'),src,format='GTiff',geoloc=True,
            dstSRS=reference.GetProjection(),outputBounds=[gt[0],gt[3]+ny*gt[5],gt[0]+nx*gt[1],gt[3]],
            width=nx,height=ny,resampleAlg='near',srcNodata=float('nan'),dstNodata=float('nan'),
            errorThreshold=0,warpMemoryLimit=256,warpOptions=['UNIFIED_SRC_NODATA=YES'])
        phase_out = raster(gdal,tmp/'phase_unwrapped.tif',nx,ny,1)
        component_out = gdal.GetDriverByName('GTiff').Create(str(tmp/'connected_components.tif'),nx,ny,1,gdal.GDT_Byte,
            options=['TILED=YES','COMPRESS=DEFLATE'])
        component_out.GetRasterBand(1).SetNoDataValue(0)
        phase_out.GetRasterBand(1).SetUnitType('radian')
        for ds in (phase_out,component_out):
            ds.SetGeoTransform(gt);ds.SetProjection(reference.GetProjection())
            ds.SetMetadata({'PAIR':source.name,'RESAMPLING':'near',
                'NOTE':'Component IDs are local to each pair; offsets between disconnected components are undetermined.'})
        phase_out.SetMetadataItem('PHASE_SIGN','arg(earlier * conjugate(later)); ISCE2 native')
        valid_count=0;counts={}
        for y in range(0,ny,128):
            a = warped.ReadAsArray(0,y,nx,min(128,ny-y))
            valid = np.isfinite(a[0]) & np.isfinite(a[1]) & (a[1]>0)
            phase_out.GetRasterBand(1).WriteArray(np.where(valid,a[0],np.nan),0,y)
            lab=np.where(valid,a[1],0).astype('uint8')
            component_out.GetRasterBand(1).WriteArray(lab,0,y)
            valid_count+=int(valid.sum())
            ids,nums=np.unique(lab[valid],return_counts=True)
            for k,v in zip(ids,nums):counts[str(k)]=counts.get(str(k),0)+int(v)
        phase_out=None;component_out=None;ds=None;warped=None;src=None
        if not valid_count:
            raise WorkspaceError('出力範囲に有効なアンラップ連結成分がありません。')
        for path in outputs:(tmp/path.name).rename(path)
    return {'phase':'phase_unwrapped.tif','components':'connected_components.tif',
            'resampling':'near','valid_pixels':valid_count,'component_pixels':counts,
            'unit':'radian','reference':'No displacement reference applied; separate component offsets are undetermined.'}
