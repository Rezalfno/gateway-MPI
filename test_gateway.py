import tempfile,time,unittest,threading,os,sys,types
from pathlib import Path
from unittest.mock import Mock,patch
from gateway import Gateway,controller,tag,raw_value,SimDriver
from modbus_driver import encode,decode,ModbusDriver,BUSES
from common import group_payload,payload

def c(id='s7',transport='MPI',**kw):return dict(id=id,name=id,transport=transport,mode='demo',interval=200,timeout=100,**kw)
def t(id='tag',cid='s7',**kw):return dict(dict(id=id,controller_id=cid,name=id,asset_id='TEST-01',tag_code='SMM.TEST-01.'+id,address='DB3.DBW0',type='INT',access='READ / WRITE',scale=1,enabled=True),**kw)
def wait(fn,timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return
        time.sleep(.02)
    raise AssertionError('Timed out waiting for condition')
class GatewayTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.g=Gateway(Path(self.tmp.name)/'cfg.json');self.g.publisher.publish=Mock();self.g.log=lambda *args:None
    def tearDown(self):self.g.close();self.tmp.cleanup()
    def act(self,a,**d):return self.g.action(a,d)
    def add(self):self.act('controller_save',**c());self.act('tag_save',**t());self.act('connect',id='s7');wait(lambda:'tag' in self.g.snapshot()['values'])
    def test_hot_edit(self):
        self.add();w=self.g.workers['s7'];self.act('tag_save',**t(address='DB3.DBW2',scale=.1));wait(lambda:'tag' in self.g.snapshot()['values']);self.assertIs(w,self.g.workers['s7']);self.assertTrue(w.thread.is_alive());self.assertEqual(self.g.tags['tag']['address'],'DB3.DBW2')
        self.act('tag_save',**t('tag2',address='DB3.DBW4'));wait(lambda:'tag2' in self.g.snapshot()['values']);self.act('tag_delete',id='tag');self.assertNotIn('tag',self.g.snapshot()['values'])
    def test_disable_live(self):
        self.add();self.act('tag_save',**t(enabled=False));time.sleep(.3);self.assertNotIn('tag',self.g.snapshot()['values'])
    def test_multiple_controllers_and_move(self):
        self.add();self.act('controller_save',**c('vfd','Modbus RTU',port_name='COM3'));self.act('tag_save',**t('vfdtag','vfd',address='3202',type='INT',area='holding',scale=.1));self.act('connect',id='vfd');wait(lambda:'vfdtag' in self.g.snapshot()['values']);self.assertEqual(len([w for w in self.g.workers.values() if w.thread.is_alive()]),2);self.act('tag_save',**t('tag','vfd',address='10',area='input',access='READ'));wait(lambda:'tag' in self.g.snapshot()['values']);self.assertEqual(self.g.tags['tag']['controller_id'],'vfd')
    def test_sim_write(self):
        self.add();self.act('write',id='tag',value=42,confirm=True);wait(lambda:self.g.snapshot()['values'].get('tag',{}).get('v')==42)
    def test_active_controller_edit_rejected(self):
        self.add();self.assertRaises(ValueError,self.act,'controller_save',**c())
    def test_persistence(self):
        self.act('controller_save',**c());self.act('tag_save',**t());g2=Gateway(self.g.path);self.assertEqual(g2.tags,self.g.tags);self.assertEqual(g2.controllers,self.g.controllers);g2.close()
    def test_mqtt_mode(self):
        self.add();wait(lambda:self.g.publisher.publish.called);args,kw=self.g.publisher.publish.call_args;self.assertTrue(kw['simulation']);self.assertEqual(args[0][0]['quality'],'SIMULATED');self.assertEqual(set(args[0][0]),{'asset_id','tag_code','source_ts','value_number','value_text','quality','gateway_id','message_id'});self.assertIn('pt_smm/simulation/TEST-01',group_payload(args[0],True))
    def test_invalid_modbus_mapping(self):
        cc=controller(c('vfd','Modbus RTU'));self.assertRaises(ValueError,tag,t(cid='vfd',address='65535',type='REAL'),cc);self.assertRaises(ValueError,tag,t(cid='vfd',address='1',area='input'),cc)
    def test_s7_conflict(self):
        self.g.factory=SimDriver;self.act('controller_save',**dict(c(),mode='real'));self.act('controller_save',**dict(c('s72','PROFIBUS DP'),mode='real'));self.act('connect',id='s7');self.assertRaises(ValueError,self.act,'connect',id='s72')
    def test_read_while_slow(self):
        event=threading.Event()
        class Slow(SimDriver):
            def read(self,t):event.set();time.sleep(.5);return 22
        self.g.factory=Slow;self.act('controller_save',**c());self.act('tag_save',**t());self.act('connect',id='s7');self.assertTrue(event.wait(2));start=time.monotonic();self.act('tag_save',**t(address='DB3.DBW2'));self.assertLess(time.monotonic()-start,.3);self.assertNotIn('tag',self.g.snapshot()['values'])
    def test_real_write_disabled(self):
        self.g.factory=SimDriver;self.act('controller_save',**dict(c(),mode='real'));self.act('tag_save',**t());self.act('connect',id='s7');wait(lambda:self.g.snapshot()['states']['s7']['state']=='Online');withenv=patch.dict(os.environ,{'MPI_ENABLE_WRITE':'0'});withenv.start()
        try:self.assertRaises(ValueError,self.act,'write',id='tag',value=10,confirm=True)
        finally:withenv.stop()
class ModbusTests(unittest.TestCase):
    def test_orders(self):
        for order in ['ABCD','BADC','CDAB','DCBA']:
            for typ,v in [('REAL',123.25),('INT',-27),('UINT',32000),('DINT',-123456),('UDINT',123456789)]:
                d=dict(type=typ,order=order);self.assertEqual(decode(d,encode(d,v)),v)
    def test_shared_bus_and_fc(self):
        cl=Mock();cl.connected=True;resp=Mock();resp.isError.return_value=False;resp.registers=[1234];cl.read_holding_registers.return_value=resp;cl.read_input_registers.return_value=resp;cl.write_register.return_value=resp
        module=types.ModuleType('pymodbus.client');module.ModbusSerialClient=Mock(return_value=cl);module.ModbusTcpClient=Mock(return_value=cl)
        with patch.dict(sys.modules,{'pymodbus':types.ModuleType('pymodbus'),'pymodbus.client':module}):
            a=ModbusDriver(controller(c('a','Modbus RTU',unit_id=1)));b=ModbusDriver(controller(c('b','Modbus RTU',unit_id=2)))
            try:
                self.assertEqual(module.ModbusSerialClient.call_count,1);d=dict(address='3202',area='holding',type='INT',order='ABCD');self.assertEqual(a.read(d),1234);cl.read_holding_registers.assert_called_with(3202,count=1,slave=1);self.assertEqual(b.read(dict(d,area='input')),1234);cl.read_input_registers.assert_called_with(3202,count=1,slave=2);b.write(d,12);cl.write_register.assert_called_with(3202,12,slave=2)
            finally:a.close();b.close()
            self.assertEqual(BUSES,{});cl.close.assert_called_once()
if __name__=='__main__':unittest.main()
