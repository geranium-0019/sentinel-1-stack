import tempfile
from pathlib import Path
import unittest
import numpy as np
from sentinel_1_stack.unwrapped_export import export_unwrapped, require_unwrap_complete
from sentinel_1_stack.workspace import WorkspaceError


class UnwrappedExportTests(unittest.TestCase):
    def test_completion_required(self):
        steps=[('run_16_unwrap',[])]
        for status in ('failed','running','interrupted'):
            with self.assertRaises(WorkspaceError):
                require_unwrap_complete({'steps':{'run_16_unwrap':{'status':status}}},steps)
        with self.assertRaises(WorkspaceError):require_unwrap_complete({'steps':{}},[])
        require_unwrap_complete({'steps':{'run_16_unwrap':{'status':'complete'}}},steps)

    def fixture(self,root):
        from osgeo import gdal,osr
        source=root/'source';geometry=root/'geometry';dest=root/'out'
        for p in (source,geometry,dest):p.mkdir()
        phase=np.arange(64,dtype='float32').reshape(8,8)*2
        phase[2,2]=0
        labels=np.ones((8,8),dtype='uint8');labels[:,4:]=2;labels[3,3]=0
        phase[:,4:]+=1000 # disconnected component has a different unknown offset
        bil=np.stack([np.ones_like(phase),phase],axis=1)
        bil.tofile(source/'filt_fine.unw');labels.tofile(source/'filt_fine.unw.conncomp')
        for name,dtype,bands in [('filt_fine.unw','Float32',2),('filt_fine.unw.conncomp','Byte',1)]:
            size=4 if bands==2 else 1
            xml='<VRTDataset rasterXSize="8" rasterYSize="8">'
            for i in range(bands):
                xml+=f'<VRTRasterBand dataType="{dtype}" band="{i+1}" subClass="VRTRawRasterBand"><SourceFilename relativeToVRT="1">{name}</SourceFilename><ImageOffset>{i*8*size}</ImageOffset><PixelOffset>{size}</PixelOffset><LineOffset>{8*size*bands}</LineOffset><ByteOrder>LSB</ByteOrder></VRTRasterBand>'
            (source/(name+'.vrt')).write_text(xml+'</VRTDataset>')
        for name,array in [('lat.rdr',np.broadcast_to((1-(np.arange(8)+.5)*.001)[:,None],(8,8))),('lon.rdr',np.broadcast_to((10+(np.arange(8)+.5)*.001)[None,:],(8,8)))]:
            ds=gdal.GetDriverByName('GTiff').Create(str(geometry/name),8,8,1,gdal.GDT_Float64)
            ds.GetRasterBand(1).WriteArray(np.ascontiguousarray(array));ds=None
            ds=gdal.Translate(str(geometry/name)+'.vrt',str(geometry/name),format='VRT');ds=None
        ds=gdal.GetDriverByName('GTiff').Create(str(dest/'phase_final.tif'),8,8,1,gdal.GDT_Float32)
        ds.SetGeoTransform((10,.001,0,1,0,-.001));s=osr.SpatialReference();s.ImportFromEPSG(4326);ds.SetProjection(s.ExportToWkt());ds=None
        return source,geometry,dest,phase,labels

    def test_unwrapped_values_zero_and_component_boundaries(self):
        from osgeo import gdal
        with tempfile.TemporaryDirectory() as tmp:
            source,geo,dest,phase,labels=self.fixture(Path(tmp))
            result=export_unwrapped(source,geo,dest)
            ds=gdal.Open(str(dest/'phase_unwrapped.tif'));a=ds.ReadAsArray()
            lab=gdal.Open(str(dest/'connected_components.tif')).ReadAsArray()
            np.testing.assert_allclose(a[1:-1,1:-1],np.where(labels>0,phase,np.nan)[1:-1,1:-1],atol=1e-5)
            np.testing.assert_array_equal(lab[1:-1,1:-1],labels[1:-1,1:-1])
            self.assertEqual(a[2,2],0);self.assertTrue(np.isnan(a[3,3]))
            self.assertEqual(ds.GetRasterBand(1).GetUnitType(),'radian')
            self.assertEqual(result['resampling'],'near')
            self.assertEqual(ds.GetGeoTransform(),gdal.Open(str(dest/'phase_final.tif')).GetGeoTransform())

    def test_truncated_or_missing_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,geo,dest,_,_=self.fixture(Path(tmp))
            p=source/'filt_fine.unw';p.write_bytes(p.read_bytes()[:-4])
            with self.assertRaisesRegex(WorkspaceError,'サイズ'):export_unwrapped(source,geo,dest)
            self.assertFalse((dest/'phase_unwrapped.tif').exists())
            (source/'filt_fine.unw.conncomp').unlink()
            with self.assertRaisesRegex(WorkspaceError,'必要'):export_unwrapped(source,geo,dest)
