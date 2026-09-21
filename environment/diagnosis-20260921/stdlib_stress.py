import argparse, faulthandler, json, os, sys, time
import xml.etree.ElementTree as ET
from unittest.mock import patch
faulthandler.enable()
faulthandler.dump_traceback_later(120, exit=True)
if len(sys.argv)>1:
    for module in sys.argv[1].split(','):
        __import__(module)
print(sys.version, 'imports', sys.argv[1:], flush=True)
start=time.monotonic()
for i in range(10000):
    parser=argparse.ArgumentParser()
    parser.add_argument('config'); parser.add_argument('--out')
    assert parser.parse_args(['file','--out','dir']).out=='dir'
    root=ET.Element('root')
    for j in range(20): ET.SubElement(root,'child', key=str(j)).text=str(j)
    assert len(ET.fromstring(ET.tostring(root)))==20
    with patch('os.getcwd', return_value='test'):
        assert os.getcwd()=='test'
    if i%1000==0: print(i, round(time.monotonic()-start,2), flush=True)
print('PASS',flush=True)
