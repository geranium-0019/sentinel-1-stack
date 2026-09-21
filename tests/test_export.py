from pathlib import Path
import tempfile
import unittest
import numpy as np
from sentinel_1_stack.export import circular_components, reconstruct, export_pair


class ExportTests(unittest.TestCase):
    def test_wrap_boundary_is_not_averaged_to_zero(self):
        z=np.exp(1j*np.deg2rad([179.,-179.])).astype('complex64')
        bands=circular_components(z,z,np.ones(2,dtype='float32')).mean(axis=1,keepdims=True)
        self.assertAlmostEqual(abs(float(reconstruct(bands)[0][0])),np.pi,places=5)

    def test_zero_phase_and_zero_coherence_are_valid_but_missing_complex_is_not(self):
        z=np.array([1+0j,0+0j],dtype='complex64')
        result=reconstruct(circular_components(z,z,np.array([0.,1.])))
        self.assertEqual(result[0][0],0.)
        self.assertEqual(result[2][0],0.)
        self.assertTrue(np.isnan(result[0][1]))

    def test_geolocation_pixel_centers_and_common_grid(self):
        from osgeo import gdal
        gdal.UseExceptions()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source';geometry=root/'geometry'
            source.mkdir();geometry.mkdir()
            phase=np.linspace(-2,2,64).reshape(8,8)
            z=np.exp(1j*phase).astype('complex64')
            inputs=[(source/'fine.int',z),(source/'filt_fine.int',z),
                    (source/'filt_fine.cor',np.full((8,8),0.8,dtype='float32')),
                    (geometry/'lat.rdr',np.broadcast_to((1-(np.arange(8)+.5)*.001)[:,None],(8,8))),
                    (geometry/'lon.rdr',np.broadcast_to((10+(np.arange(8)+.5)*.001)[None,:],(8,8)))]
            for path,array in inputs:
                dtype=gdal.GDT_CFloat32 if np.iscomplexobj(array) else gdal.GDT_Float64
                ds=gdal.GetDriverByName('GTiff').Create(str(path),8,8,1,dtype)
                ds.GetRasterBand(1).WriteArray(np.ascontiguousarray(array));ds=None
                ds=gdal.Translate(str(path)+'.vrt',str(path),format='VRT');ds=None
            result=export_pair(source,geometry,root/'out',[.992,1.,10.,10.008],.001,'near')
            ds=gdal.Open(str(root/'out/phase_final.tif'))
            np.testing.assert_allclose(ds.ReadAsArray()[1:-1,1:-1],phase[1:-1,1:-1],atol=1e-6)
            other=gdal.Open(str(root/'out/coherence.tif'))
            self.assertEqual(ds.GetGeoTransform(),other.GetGeoTransform())
            self.assertEqual(result['grid']['width'],8)
