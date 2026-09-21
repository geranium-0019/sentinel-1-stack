import faulthandler,sys,unittest
faulthandler.enable();faulthandler.dump_traceback_later(60,exit=True)
sys.path.insert(0,'/project/tests')
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_prepare','test_runner'])
print('native modules:',[k for k,v in sys.modules.items() if str(getattr(v,'__file__','')).endswith('.so')],flush=True)
r=unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not r.wasSuccessful())
