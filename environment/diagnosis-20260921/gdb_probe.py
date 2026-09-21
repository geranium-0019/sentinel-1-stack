import faulthandler,sys,unittest
faulthandler.enable()
sys.path.insert(0,'/project/tests')
for i in range(40):
 print('ITERATION',i,flush=True)
 suite=unittest.defaultTestLoader.discover('/project/tests')
 result=unittest.TextTestRunner(verbosity=0).run(suite)
 if not result.wasSuccessful():sys.exit(1)
print('DONE',flush=True)
