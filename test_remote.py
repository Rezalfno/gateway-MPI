import base64, hashlib, http.client, json, tempfile, threading, unittest, os
from pathlib import Path
from unittest.mock import Mock
from gateway import Gateway,GatewayHTTPServer,Handler
from remote_access import RemotePolicy
from test_gateway import c,t,wait

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.g=Gateway(Path(self.temp.name)/'cfg.json');self.g.publisher.publish=Mock();self.g.log=lambda *args:None
        self.g.action('controller_save',c());self.g.action('tag_save',t());self.g.action('connect',{'id':'s7'})
        self.server=GatewayHTTPServer(('127.0.0.1',0),Handler);self.server.gateway=self.g
        self.port=self.server.server_address[1];self.policy=RemotePolicy(dict(bind_ip='127.0.0.1',port=self.port,allowed_network='127.0.0.1/32',username='test',password_sha256=hashlib.sha256(b'example').hexdigest(),tls=False,allow_insecure_http=True));self.server.remote_policy=self.policy
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.g.close();self.temp.cleanup()
    def req(self,path='/api/status',data=None,auth=True,origin=True,host=None):
        h={'Host':host or self.policy.host}
        if auth:h['Authorization']='Basic '+base64.b64encode(b'test:example').decode()
        if data is not None:
            h.update({'Content-Type':'application/json','X-Gateway-Request':'1'})
            if origin:h['Origin']=self.policy.origin
        con=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        try:
            con.request('GET' if data is None else 'POST',path,body=None if data is None else json.dumps(data),headers=h);r=con.getresponse();return r.status,json.loads(r.read())
        finally:con.close()
    def test_auth_host_origin(self):
        self.assertEqual(self.req(auth=False)[0],401);self.assertEqual(self.req(host='evil.example')[0],403);self.assertEqual(self.req('/api/tag_save',t('new'),origin=False)[0],403);self.assertFalse(self.policy.permits('192.168.1.1',self.policy.host));self.assertFalse(self.policy.authenticated('Basic bad!'))
    def test_remote_hot_edit_and_demo_write(self):
        status,data=self.req();self.assertEqual(status,200);self.assertTrue(data['remote']);self.assertFalse(data['real_write']);self.assertEqual(self.req('/api/tag_save',t('new',address='DB3.DBW4'))[0],200);wait(lambda:'new' in self.g.values);self.assertEqual(self.req('/api/write',{'id':'new','value':10,'confirm':True})[0],200);wait(lambda:self.g.values.get('new',{}).get('v')==10)
    def test_real_write_denied(self):
        self.g.action('controller_save',dict(c('real'),mode='real'));self.g.action('tag_save',t('realTag','real'))
        self.assertEqual(self.req('/api/write',{'id':'realTag','value':1,'confirm':True})[0],403);self.assertEqual(self.req('/api/tag_save',t('realTag','real',address='DB3.DBW6'))[0],403);self.assertEqual(self.req('/api/tag_delete',{'id':'realTag'})[0],403)
        self.assertEqual(self.req('/api/tag_save',t('readTag','real',access='READ'))[0],200)
    def test_resume_disconnect(self):
        g2=Gateway(self.g.path);g2.publisher.start=Mock();g2.publisher.publish=Mock();g2.log=lambda *a:None
        try:
            self.assertEqual(g2.desired,{'s7'});g2.start();wait(lambda:'tag' in g2.values);g2.action('disconnect',{'id':'s7'});self.assertEqual(json.loads(g2.path.read_text())['resume_controllers'],[])
        finally:g2.close()
    def test_insecure_opt_in(self):
        with self.assertRaises(ValueError):RemotePolicy(dict(bind_ip='192.168.1.5',allowed_network='192.168.1.0/24',username='x',password_sha256='a'*64,tls=False))

if __name__=='__main__':unittest.main()
