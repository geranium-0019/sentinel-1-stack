"""Exercise compiled SNAPHU on known wrapped phase; this is not real-data acceptance."""
import os
from pathlib import Path
import tempfile
import numpy as np
import isce
import isceobj
import sys
sys.path.insert(0, '/opt/conda/share/isce2/stack/topsStack')
from unwrap import runUnwrap

with tempfile.TemporaryDirectory() as tmp:
    os.chdir(tmp)
    n = 128
    y, x = np.mgrid[:n, :n]
    truth = (.1*x + .06*y).astype('float32')
    np.exp(1j*truth).astype('complex64').tofile('wrapped.int')
    np.full((n,n), .95, dtype='float32').tofile('coherence.cor')
    for name, dtype in [('wrapped.int', 'CFLOAT'), ('coherence.cor', 'FLOAT')]:
        img = isceobj.createImage()
        img.setFilename(name); img.setWidth(n); img.setLength(n)
        img.setDataType(dtype); img.setAccessMode('read'); img.renderHdr()
    runUnwrap('wrapped.int', 'phase.unw', 'coherence.cor',
              dict(wavelength=.05546576, earthRadius=6371000., altitude=700000.,
                   rglooks=3, azlooks=1, corrlooks=4.6875), initMethod='MCF', initOnly=True)
    phase = np.fromfile('phase.unw', dtype='float32').reshape(n,2,n)[:,1,:]
    error = phase - truth
    error -= np.median(error)
    assert np.isfinite(phase).all()
    assert np.max(np.abs(error)) < 1e-3, np.max(np.abs(error))
    labels = np.fromfile('phase.unw.conncomp', dtype='uint8').reshape(n,n)
    assert np.all(labels > 0), np.unique(labels)
    print('Compiled SNAPHU: OK; 128x128 known phase, error < 0.001 rad after constant offset')
