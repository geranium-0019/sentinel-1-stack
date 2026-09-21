import faulthandler,sys,unittest,json
faulthandler.enable()
faulthandler.dump_traceback_later(20,exit=True)
mode=sys.argv[1]
if mode=='suite':
 suite=unittest.defaultTestLoader.discover('/project/tests')
 assert suite.countTestCases() > 0, 'No tests discovered'
 print('Discovered tests:', suite.countTestCases(), flush=True)
 print('imagecodecs loaded before suite:', 'imagecodecs' in sys.modules,flush=True)
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 print('imagecodecs loaded after suite:', 'imagecodecs' in sys.modules,flush=True)
 sys.exit(not result.wasSuccessful())
elif mode=='codec':
 import numpy as np, imagecodecs as c, tifffile, io
 print('numpy',np.__version__,'imagecodecs',c.__version__,flush=True)
 a=np.arange(64*64,dtype='uint16').reshape(64,64)
 for name in ('zlib','lzma','zstd','lzw'):
  b=io.BytesIO();tifffile.imwrite(b,a,compression=name);b.seek(0)
  np.testing.assert_array_equal(tifffile.imread(b),a)
  print(name,'roundtrip OK',flush=True)
elif mode=='stdlib':
 import re,xml.etree.ElementTree as e,unittest.mock as m
 for i in range(20000):
  assert re.fullmatch(r'(abc|def)+','abcdef')
  assert e.fromstring('<a><b>42</b></a>').findtext('b')=='42'
  with m.patch('os.getcwd',return_value='test'):
   import os
   assert os.getcwd()=='test'
 print('stdlib 20000 iterations OK',flush=True)
