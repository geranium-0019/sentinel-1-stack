import faulthandler,sys,unittest,runpy
faulthandler.enable();faulthandler.dump_traceback_later(60,exit=True)
def flatten(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite):yield from flatten(item)
  else:yield item
alltests=unittest.defaultTestLoader.discover('/project/tests')
geo={'test_dem','test_export','test_unwrapped_export'}
mode=sys.argv[1]
suite=unittest.TestSuite(t for t in flatten(alltests) if (t.id().split('.')[0] in geo)==(mode=='geo'))
print(mode,suite.countTestCases(),flush=True)
r=unittest.TextTestRunner(verbosity=1).run(suite)
if not r.wasSuccessful():sys.exit(1)
if mode=='geo':
 sys.argv=['stress'];runpy.run_path('/diagnosis/stdlib_stress.py',run_name='__main__')
