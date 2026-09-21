import faulthandler,sys
faulthandler.enable();faulthandler.dump_traceback_later(15,exit=True)
import xml.etree.ElementTree as ET
from datetime import datetime,timedelta
if len(sys.argv)>1:
 for module in sys.argv[1].split(','):__import__(module)
print('start',sys.version,flush=True)
for iteration in range(200):
 root=ET.Element('Earth_Explorer_File')
 h=ET.SubElement(ET.SubElement(root,'Earth_Explorer_Header'),'Fixed_Header')
 for k,v in {'File_Name':'S1D_OPER_AUX_POEORB','File_Type':'AUX_POEORB','Mission':'Sentinel-1D'}.items():ET.SubElement(h,k).text=v
 vectors=ET.SubElement(ET.SubElement(root,'Data_Block'),'List_of_OSVs')
 for second in range(0,2401,10):
  vector=ET.SubElement(vectors,'OSV');ET.SubElement(vector,'UTC').text='UTC='+(datetime(2026,9,3)+timedelta(seconds=second)).isoformat()
  for key in ('X','Y','Z','VX','VY','VZ'):ET.SubElement(vector,key,unit='m/s' if key.startswith('V') else 'm').text='1.5'
  ET.SubElement(vector,'Quality').text='NOMINAL'
 vectors.set('count',str(len(vectors)))
 data=ET.tostring(root);assert len(ET.fromstring(data).find('Data_Block/List_of_OSVs'))==241
print('200 XML round trips OK',flush=True)
