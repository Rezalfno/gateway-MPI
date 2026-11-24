"""Multi-controller industrial gateway. Local HTTP; I/O outside configuration lock."""
import copy,json,math,os,queue,re,struct,threading,time,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from plc_driver import IsolatedDriver, Demo, address, FORMATS, RATES
from modbus_driver import ModbusDriver, count, serial_signature, ORDERS
from common import utc,integer,string,payload,Publisher,mqtt_enabled
import mqtt_config as mc
from remote_access import start_remote
import sys,logging,logging.handlers,socket,ssl
ROOT=Path(__file__).resolve().parent
S7=set(RATES); PROTOCOLS=S7|{'Modbus TCP','Modbus RTU'}

def ident(v):
    v=string(v,'ID',64)
    if not re.fullmatch(r'[\w.-]+',v,flags=re.ASCII): raise ValueError('Invalid ID')
    return v

def controller(x):
    p=x.get('transport','MPI'); mode=x.get('mode','demo')
    if p not in PROTOCOLS or mode not in ('demo','real'): raise ValueError('Invalid protocol/mode')
    c=dict(id=ident(x['id']),name=string(x['name'],'Controller name'),transport=p,mode=mode,timeout=integer(x.get('timeout',1500),100,10000,'timeout'),interval=integer(x.get('interval',1000),200,60000,'polling interval'))
    if p in S7:
        c.update(access=ident(x.get('access','S7ONLINE')),local=integer(x.get('local',0),0,126,'local address'),remote=integer(x.get('remote',2),0,126,'PLC address'),baud=x.get('baud','187.5 kbps'))
        if c['local']==c['remote'] or c['baud'] not in RATES[p]: raise ValueError('Invalid station / baud')
    else:
        c['unit_id']=integer(x.get('unit_id',1),1,247 if p=='Modbus RTU' else 255,'unit ID')
        if p=='Modbus TCP': c.update(host=string(x.get('host',''),'host'),tcp_port=integer(x.get('tcp_port',502),1,65535,'TCP port'))
        else:
            c.update(port_name=string(x.get('port_name','COM3'),'COM port').upper(),baudrate=integer(x.get('baudrate',19200),1200,115200,'baud rate'),parity=x.get('parity','E'),stopbits=integer(x.get('stopbits',1),1,2,'stop bits'))
            if not re.fullmatch(r'COM[1-9]\d*',c['port_name']) or c['parity'] not in ('N','E','O') or c['baudrate'] not in (1200,2400,4800,9600,19200,38400,57600,115200): raise ValueError('Invalid serial configuration')
    return c

def tag(x,c):
    t={k:string(x.get(k),k,256 if k=='tag_code' else 128) for k in ('id','controller_id','name','asset_id','tag_code','address','type','access')}; t['id']=ident(t['id']); t['asset_id']=ident(t['asset_id'])
    if t['access'] not in ('READ','READ / WRITE') or t['type'] not in FORMATS: raise ValueError('Invalid type/access')
    t['unit']=str(x.get('unit',''))[:40]; t['scale']=float(x.get('scale',1)); t['enabled']=x.get('enabled',True)
    if not isinstance(t['enabled'],bool) or not math.isfinite(t['scale']) or t['scale']==0 or (t['type']=='BOOL' and t['scale']!=1): raise ValueError('Invalid scale / enabled')
    if c['transport'] in S7:
        t['address']=t['address'].upper(); address(t)
    else:
        t['area']=x.get('area','holding'); t['order']=x.get('order','ABCD'); t['address']=str(integer(t['address'],0,65535,'zero-based Modbus address'))
        if t['area'] not in ('holding','input','coil','discrete') or t['order'] not in ORDERS: raise ValueError('Invalid Modbus area/order')
        if (t['area'] in ('coil','discrete')) != (t['type']=='BOOL') or t['type']=='BYTE': raise ValueError('Use BOOL for bits; INT/UINT/DINT/UDINT/REAL for registers')
        if int(t['address'])+count(t)>65536: raise ValueError('Register range exceeds limit')
        if t['area'] in ('input','discrete') and t['access']!='READ': raise ValueError('Input registers/discrete inputs are read only')
    for k in ('write_min','write_max'):
        v=x.get(k); t[k]=None if v in (None,'') else float(v)
        if t[k] is not None and not math.isfinite(t[k]): raise ValueError('Invalid write limit')
    if all(t[k] is not None for k in ('write_min','write_max')) and t['write_min']>t['write_max']: raise ValueError('Invalid write limits')
    return t

def raw_value(t,v):
    v=float(v); raw=v/t['scale']
    if not math.isfinite(raw): raise ValueError('Nonfinite value')
    if t['type']!='REAL':
        if not math.isclose(raw,round(raw),rel_tol=0,abs_tol=1e-7): raise ValueError('Value must match integer raw scaling')
        raw=round(raw)
    if t['type']=='BOOL' and raw not in (0,1): raise ValueError('BOOL: 0 or 1')
    try: struct.pack('>'+FORMATS[t['type']],raw)
    except (struct.error,OverflowError): raise ValueError('Value outside data type range')
    return raw

class SimDriver(Demo):
    def __init__(self,c): super().__init__(c); self.n=0
    def read(self,t):
        key=(t.get('area','S7'),t['address'],t['type'])
        if key in self.values:return self.values[key]
        self.n+=1
        return int(self.n//5%2) if t['type']=='BOOL' else (round(100+10*math.sin(self.n/5),3) if t['type']=='REAL' else round(100+10*math.sin(self.n/5)))
    def write(self,t,v): self.values[(t.get('area','S7'),t['address'],t['type'])]=v

def factory(c):
    if c['mode']=='demo':return SimDriver(c)
    return IsolatedDriver(c) if c['transport'] in S7 else ModbusDriver(c)

class Worker:
    def __init__(self,g,c):
        self.g=g; self.c=copy.deepcopy(c); self.stop=threading.Event(); self.wake=threading.Event(); self.writes=queue.Queue(1); self.thread=threading.Thread(target=self.run,daemon=True); self.state='Connecting'; self.error=''; self.cycles=0; self.ms=0; self.driver=None; self.writing=None; self.paused=False
    def status(self,state,error=''):
        with self.g.lock:self.state=state; self.error=error
    def close(self):
        d,self.driver=self.driver,None
        if d:
            try:d.close()
            except Exception as e:self.g.log(self.c['name']+': close '+str(e))
    def do_write(self):
        try: t,v,raw,revision=self.writes.get_nowait()
        except queue.Empty:return
        g=self.g; k=t['id']; started=False
        try:
            with g.lock:
                if self.stop.is_set() or self.paused or g.revs.get(k)!=revision or self.state not in ('Online','Simulation'):raise ValueError('Write cancelled before dispatch')
                last=g.values.get(k,{})
                from datetime import datetime,timezone
                if last.get('quality') not in ('GOOD','SIMULATED') or (datetime.now(timezone.utc)-datetime.fromisoformat(last['time'])).total_seconds()>max(5,2*self.c['interval']/1000):raise ValueError('Write cancelled: stale reading')
                if self.c['mode']=='real' and os.getenv('MPI_ENABLE_WRITE')!='1':raise ValueError('Real write disabled')
                self.writing=k; started=True
            self.driver.write(t,raw); actual=self.driver.read(t)*t['scale']
            if not math.isfinite(actual):raise ValueError('Nonfinite readback')
            with g.lock:
                g.values[k]=dict(v=actual,quality='SIMULATED' if self.c['mode']=='demo' else 'GOOD',time=utc()); matched=math.isclose(v,actual,rel_tol=1e-6,abs_tol=1e-7)
                g.log(f"{self.c['name']} WRITE {t['address']}: requested={v}, readback={actual}, matched={matched}")
                if not matched:self.paused=True; self.state='Write verify failed'; self.error='Readback mismatch; verify equipment before reconnect'; g.values[k]['quality']='BAD';g.inhibit_resume(self.c['id'])
        except Exception as e:
            with g.lock:
                if started:
                    self.paused=True; self.state='Write uncertain'; self.error=str(e); g.values.pop(k,None);g.inhibit_resume(self.c['id'])
                g.log(self.c['name']+': '+('WRITE outcome uncertain: ' if started else 'WRITE cancelled: ')+str(e))
        finally:
            with g.lock:self.writing=None
    def run(self):
        g=self.g; cid=self.c['id']; retry=0; failures=0
        try:
            while not self.stop.is_set():
                if self.paused:self.wake.wait(.2);self.wake.clear();continue
                if self.driver is None:
                    try:
                        self.status('Connecting'); self.driver=g.factory(self.c); self.status('Simulation' if self.c['mode']=='demo' else 'Awaiting read'); failures=0;g.log(self.c['name']+': session opened')
                    except Exception as e:
                        retry+=1; self.status('Reconnecting',str(e));g.log(self.c['name']+': '+str(e));self.stop.wait(min(60,5*2**min(retry-1,4)));continue
                self.do_write()
                if self.paused:continue
                started=time.monotonic(); batch=[]; good=0; bad=0; err=''
                with g.lock: items=[(copy.deepcopy(t),g.revs[k]) for k,t in g.tags.items() if t['controller_id']==cid and t['enabled']]
                for t,rev in items:
                    if self.stop.is_set() or self.paused:break
                    self.do_write()
                    if self.paused:break
                    k=t['id']
                    with g.lock:
                        if g.revs.get(k)!=rev:continue
                    try:
                        v=self.driver.read(t)*t['scale']
                        if not math.isfinite(v):raise ValueError('Nonfinite read')
                        q='SIMULATED' if self.c['mode']=='demo' else 'GOOD';good+=1
                    except Exception as e:v=None;q='BAD';bad+=1;err=str(e)
                    ts=utc()
                    with g.lock:
                        if g.revs.get(k)==rev and not self.stop.is_set():
                            g.values[k]=dict(v=v,quality=q,time=ts);batch.append((k,rev,payload(t,v,q,ts)))
                    # Avoid a long series of timeouts on dead equipment.
                    if bad>=3 and good==0:break
                with g.lock:
                    if not self.stop.is_set() and not self.paused:
                        self.cycles+=1;self.ms=round((time.monotonic()-started)*1000)
                        if good:retry=0;failures=0;self.state='Degraded' if bad else ('Simulation' if self.c['mode']=='demo' else 'Online'); self.error=err
                        elif bad:failures+=1;self.state='Read failed';self.error=err
                        else:self.state='Simulation' if self.c['mode']=='demo' else 'Awaiting read';self.error=''
                        if bad and not good:
                            for t,rev in items:
                                if g.revs.get(t['id'])==rev and not any(k==t['id'] for k,_,_ in batch):g.values[t['id']]=dict(v=None,time=utc(),quality='BAD')
                        g.publisher.publish([p for k,r,p in batch if g.revs.get(k)==r],simulation=self.c['mode']=='demo')
                if failures>=3:
                    self.close();retry+=1;self.status('Reconnecting',err);self.stop.wait(min(60,5*2**min(retry-1,4)))
                else:
                    self.wake.wait(max(.02,self.c['interval']/1000-(time.monotonic()-started)));self.wake.clear()
        finally:
            self.close();self.status('Disconnected')
            with g.lock:
                for k,t in g.tags.items():
                    if t['controller_id']==cid:g.values.pop(k,None)

class Gateway:
    def __init__(self,path=ROOT/'config_v6.json',driver_factory=factory):
        self.path=Path(path);self.lock=threading.RLock();self.controllers={};self.tags={};self.desired=set();self.revs={};self.rev=0;self.values={};self.workers={};self.logs=[];self.factory=driver_factory;self.publisher=Publisher(self.log)
        if self.path.exists():
            d=json.loads(self.path.read_text(encoding='utf8'));self.controllers={c['id']:controller(c) for c in d['controllers']}; self.tags={t['id']:tag(t,self.controllers[t['controller_id']]) for t in d['tags']}
            self.revs={k:0 for k in self.tags}; self.desired=set(d.get('resume_controllers',[])) & self.controllers.keys()
        elif self.path==ROOT/'config_v6.json' and (ROOT/'config.json').exists():
            d=json.loads((ROOT/'config.json').read_text(encoding='utf8'));c=controller(dict(d['config'],id='legacy-s7'));self.controllers[c['id']]=c
            for x in d['tags']:
                x=dict(x,controller_id=c['id']); x.setdefault('asset_id',d['config'].get('asset_id','LEGACY'));t=tag(x,c);self.tags[t['id']]=t;self.revs[t['id']]=0
            self.persist(self.controllers,self.tags)
    def log(self,msg):
        with self.lock:self.logs.insert(0,utc()+'  '+str(msg));del self.logs[200:]
        print(msg,flush=True)
    def persist(self,cs,ts,desired=None):
        tmp=self.path.with_suffix('.tmp');
        with tmp.open('w',encoding='utf8') as f:json.dump(dict(controllers=list(cs.values()),tags=list(ts.values()),resume_controllers=sorted((self.desired if desired is None else desired)&cs.keys())),f,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,self.path)
    def set_desired(self,ids):
        self.persist(self.controllers,self.tags,ids);self.desired=set(ids)
    def inhibit_resume(self,k):
        # A failed physical write must not be retried or resumed automatically.
        try:self.set_desired(self.desired-{k})
        except Exception as e:self.log("Cannot persist write lockout: "+str(e))
    def active(self,k):return k in self.workers and self.workers[k].thread.is_alive()
    def snapshot(self):
        with self.lock:
            states={k:dict(state=w.state,error=w.error,cycles=w.cycles,scan_ms=w.ms,active=w.thread.is_alive()) for k,w in self.workers.items()}
            values=copy.deepcopy(self.values)
            from datetime import datetime,timezone
            now=datetime.now(timezone.utc)
            for k,v in values.items():
                c=self.controllers[self.tags[k]['controller_id']]
                if v['quality'] in ('GOOD','SIMULATED') and (now-datetime.fromisoformat(v['time'])).total_seconds()>max(5,2*c['interval']/1000):v['quality']='STALE'
            return copy.deepcopy(dict(controllers=list(self.controllers.values()),tags=list(self.tags.values()),values=values,states=states,logs=self.logs,mqtt=dict(connected=bool(self.publisher.client and self.publisher.client.is_connected()),configured=bool(mc.MQTT_HOST),dropped=self.publisher.dropped),real_write=os.getenv('MPI_ENABLE_WRITE')=='1',resume_controllers=sorted(self.desired)))
    def action(self,a,d):
        with self.lock:
            k=d.get('id')
            if a=='controller_save':
                c=controller(d);k=c['id'];old=self.controllers.get(k)
                if self.active(k):raise ValueError('Disconnect this controller before changing physical settings')
                if old and old['transport']!=c['transport'] and any(t['controller_id']==k for t in self.tags.values()):raise ValueError('Remove or move existing tags before changing protocol')
                cs=dict(self.controllers,**{k:c});self.persist(cs,self.tags);self.controllers=cs
            elif a=='controller_delete':
                if self.active(k) or any(t['controller_id']==k for t in self.tags.values()):raise ValueError('Disconnect and remove/move its tags first')
                cs=dict(self.controllers);del cs[k];self.persist(cs,self.tags);self.controllers=cs;self.desired.discard(k);self.workers.pop(k,None)
            elif a=='tag_save':
                t=tag(d,self.controllers[d['controller_id']]);k=t['id'];old=self.tags.get(k)
                if any(w.writing==k for w in self.workers.values()):raise ValueError('Write in progress on this tag')
                if any(j!=k and (v['asset_id'],v['tag_code'])==(t['asset_id'],t['tag_code']) for j,v in self.tags.items()):raise ValueError('Duplicate asset/tag code')
                if not old and len(self.tags)>=2000:raise ValueError('Maximum 2000 tags')
                ts=dict(self.tags,**{k:t});self.persist(self.controllers,ts);self.tags=ts;self.rev+=1;self.revs[k]=self.rev;self.values.pop(k,None)
                for cid in {t['controller_id'],old['controller_id'] if old else t['controller_id']}:
                    if self.active(cid):self.workers[cid].wake.set()
            elif a=='tag_delete':
                if any(w.writing==k for w in self.workers.values()):raise ValueError('Write in progress')
                ts=dict(self.tags);t=ts.pop(k);self.persist(self.controllers,ts);self.tags=ts;self.revs.pop(k,None);self.values.pop(k,None)
                if self.active(t['controller_id']):self.workers[t['controller_id']].wake.set()
            elif a=='connect':
                c=self.controllers[k]
                if self.active(k):raise ValueError('Controller already active or disconnecting')
                if c['mode']=='real':
                    for w in self.workers.values():
                        if not w.thread.is_alive() or w.c['mode']!='real':continue
                        p=w.c
                        if c['transport'] in S7 and p['transport'] in S7:raise ValueError('One real S7 session per physical adapter; disconnect other S7 controller first')
                        if c['transport']==p['transport']=='Modbus RTU' and c['port_name']==p['port_name']:
                            if serial_signature(c)!=serial_signature(p) or c['unit_id']==p['unit_id']:raise ValueError('Same COM requires same serial settings and unique unit IDs')
                self.set_desired(self.desired|{k});w=Worker(self,c);self.workers[k]=w;w.thread.start()
            elif a=='disconnect':
                if k not in self.controllers:raise ValueError('Unknown controller')
                self.set_desired(self.desired-{k})
                if self.active(k):
                    w=self.workers[k];w.stop.set();w.wake.set();w.state='Disconnecting'
                    for tid,t in self.tags.items():
                        if t['controller_id']==k:self.values.pop(tid,None)
            elif a=='write':
                t=self.tags[k];w=self.workers.get(t['controller_id']);v=float(d['value']);raw=raw_value(t,v);last=self.values.get(k,{})
                if not w or not self.active(t['controller_id']) or w.stop.is_set() or w.paused or w.state not in ('Online','Simulation') or t['access']!='READ / WRITE' or not t['enabled'] or d.get('confirm') is not True or last.get('quality') not in ('GOOD','SIMULATED'):raise ValueError('Healthy active controller, enabled RW tag, fresh reading and confirmation required')
                from datetime import datetime,timezone
                if (datetime.now(timezone.utc)-datetime.fromisoformat(last['time'])).total_seconds()>max(5,2*w.c['interval']/1000):raise ValueError('Stale reading')
                if w.c['mode']=='real':
                    if os.getenv('MPI_ENABLE_WRITE')!='1' or t['write_min'] is None or t['write_max'] is None or not t['write_min']<=v<=t['write_max']:raise ValueError('Real write disabled or outside explicit approved tag limits')
                    if w.c['transport'] in S7 and address(t)[0] not in (0x83,0x84):raise ValueError('Real S7 write limited to DB/M')
                if w.writing or not w.writes.empty():raise ValueError('Write already pending')
                w.writes.put_nowait((copy.deepcopy(t),v,raw,self.revs[k]));w.wake.set();self.log('Write queued: '+t['name']);return {'message':'Write queued; see Event log for actual readback'}
            elif a=='clear_logs':self.logs=[]
            else:raise ValueError('Unknown action')
            return {'message':'Saved' if a.endswith('save') else 'Accepted'}
    def start(self):
        # One MQTT client for all controllers; per-scan production/simulation topic selection.
        self.publisher.start(simulation=False)
        for k in sorted(self.desired):
            try:self.action('connect',{'id':k})
            except Exception as e:self.log('Auto resume '+k+': '+str(e))
    def close(self):
        with self.lock:ws=list(self.workers.values());[w.stop.set() or w.wake.set() for w in ws]
        for w in ws:w.thread.join(timeout=12)
        self.publisher.close()

class GatewayHTTPServer(ThreadingHTTPServer):
    # TLS handshakes happen in worker threads, not the accept loop.
    daemon_threads=True
    def process_request_thread(self,request,client_address):
        try:
            request.settimeout(10)
            if getattr(self,'tls_context',None):request=self.tls_context.wrap_socket(request,server_side=True)
        except (OSError,ssl.SSLError):
            request.close();return
        super().process_request_thread(request,client_address)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send_data(self,code,data,mime='application/json',headers=None):
        if mime=='application/json':data=json.dumps(data,allow_nan=False).encode()
        self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'")
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
    def authorize(self):
        policy=getattr(self.server,'remote_policy',None);host=self.headers.get('Host','')
        if policy:
            if not policy.permits(self.client_address[0],host):self.send_data(403,{'error':'LAN access denied'});return False
            if not policy.authenticated(self.headers.get('Authorization','')):
                self.send_data(401,{'error':'Authentication required'},headers={'WWW-Authenticate':'Basic realm="Industrial Gateway", charset="UTF-8"'});return False
        elif host not in ('127.0.0.1:8765','localhost:8765'):
            self.send_data(403,{'error':'Local only'});return False
        return True
    def do_GET(self):
        if not self.authorize():return
        if self.path=='/':return self.send_data(200,(ROOT/'index.html').read_bytes(),'text/html; charset=utf-8')
        if self.path=='/api/status':
            data=self.server.gateway.snapshot();data['remote']=bool(getattr(self.server,'remote_policy',None))
            if data['remote']:data['real_write']=False
            return self.send_data(200,data)
        self.send_data(404,{'error':'Not found'})
    def do_POST(self):
        if not self.authorize():return
        policy=getattr(self.server,'remote_policy',None);origin=self.headers.get('Origin');expected=policy.origin if policy else 'http://'+self.headers.get('Host','')
        if (origin not in (None,expected) or (policy and (origin!=expected or self.headers.get('X-Gateway-Request')!='1')) or self.headers.get('Content-Type','').split(';')[0]!='application/json'):
            return self.send_data(403,{'error':'Origin / JSON request denied'})
        try:
            n=int(self.headers.get('Content-Length',0));
            if not 0<n<=1_000_000:raise ValueError('Invalid request size')
            if not self.path.startswith('/api/'):raise ValueError('Invalid API path')
            d=json.loads(self.rfile.read(n));a=self.path.removeprefix('/api/');g=self.server.gateway
            if not isinstance(d,dict):raise ValueError('Expected object')
            with g.lock:
                if policy:
                    # Remote access is for monitoring, controller read polling and READ tag management.
                    # Protect real write mappings from remote modification, including moves/deletes.
                    if a in ('write','tag_save','tag_delete'):
                        old=g.tags.get(d.get('id'));new=d if a=='tag_save' else old
                        def real(t):return bool(t and g.controllers.get(t.get('controller_id'),{}).get('mode')=='real')
                        if (a=='write' and real(old)) or (a!='write' and ((real(old) and old.get('access')=='READ / WRITE') or (real(new) and new.get('access')=='READ / WRITE'))):
                            return self.send_data(403,{'error':'Real WRITE / RW mapping requires local commissioning'})
                    if a=='controller_save':
                        # Prevent changing a demo RW controller into a real writable controller remotely.
                        if d.get('mode')=='real' and any(t['controller_id']==d.get('id') and t['access']=='READ / WRITE' for t in g.tags.values()):
                            return self.send_data(403,{'error':'Real RW controller settings require local commissioning'})
                r=g.action(a,d)
            self.send_data(200,r)
        except Exception as e:self.send_data(400,{'error':str(e)})

class LogStream:
    def __init__(self,logger,level):self.logger=logger;self.level=level
    def write(self,value):
        if value.strip():self.logger.log(self.level,value.rstrip())
        return len(value)
    def flush(self):pass

def boot_logging():
    logger=logging.getLogger('gateway.boot');logger.setLevel(logging.INFO)
    h=logging.handlers.RotatingFileHandler(ROOT/'gateway_boot.log',maxBytes=2_000_000,backupCount=3,encoding='utf8');h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logger.addHandler(h)
    sys.stdout=LogStream(logger,logging.INFO);sys.stderr=LogStream(logger,logging.ERROR)

if __name__=='__main__':
    import multiprocessing as mp
    mp.freeze_support()
    if '--boot' in sys.argv:
        boot_logging();os.environ.pop('MPI_ENABLE_WRITE',None) # unattended boot is read-only
        time.sleep(20)
    g=Gateway();server=GatewayHTTPServer(('127.0.0.1',8765),Handler);server.gateway=g;g.start();remote_close=start_remote(ROOT,GatewayHTTPServer,Handler,g);print('Industrial Gateway V7: http://127.0.0.1:8765',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        if remote_close:remote_close()
        server.server_close();g.close()
