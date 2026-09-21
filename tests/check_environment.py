"""Run in the image: exercise ASF/Shapely alongside compiled ISCE2/GDAL."""
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys


def main():
    before = Path("/opt/conda/share/isce2/python-before-download.txt")
    for item in before.read_text().splitlines():
        name, expected = item.split("==", 1)
        assert version(name) == expected, (name, expected, version(name))

    # Both orders matter when independently packaged native libraries coexist.
    for imports in (
        "import asf_search, shapely; import isce; from osgeo import gdal, ogr",
        "import isce; from osgeo import gdal, ogr; import asf_search, shapely",
    ):
        code = imports + '''
import numpy as np
import scipy, h5py
gdal.UseExceptions()
from shapely.geometry import Point
from zerodop.topozero import topozero
from contrib.Snaphu import snaphu
from isceobj.Sensor.TOPS.Sentinel1 import Sentinel1
assert np.__version__ == '1.26.4'
assert Point(0, 0).buffer(1).area > 3
assert ogr.CreateGeometryFromWkt('POINT (0 0)').Buffer(1).GetArea() > 3
ds = gdal.GetDriverByName('MEM').Create('', 2, 2, 1, gdal.GDT_Float32)
ds.GetRasterBand(1).WriteArray(np.ones((2, 2), dtype=np.float32))
assert ds.ReadAsArray().sum() == 4
with asf_search.ASFSession() as session:
    assert session.verify is True
print('ISCE2', isce.__version__, 'NumPy', np.__version__, 'ASF', asf_search.__version__)
'''
        subprocess.run([sys.executable, "-c", code], check=True)
    print("ISCE2/ASF compatibility: OK; existing Python package versions unchanged")


if __name__ == "__main__":
    main()
