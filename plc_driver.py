import ctypes as C, struct, re, os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
RATES={'MPI':['19.2 kbps','187.5 kbps','1.5 Mbps'], 'PROFIBUS DP':['9.6 kbps','19.2 kbps','45.45 kbps','93.75 kbps','187.5 kbps','500 kbps','1.5 Mbps','3 Mbps','6 Mbps','12 Mbps']}
# S7ONLINE uses the actual bus settings from PG/PC Interface.
FORMATS={'BOOL':'B','BYTE':'B','INT':'h','UINT':'H','DINT':'i','UDINT':'I','REAL':'f'}
def address(t):
    a=t['address'].upper(); m=re.fullmatch(r'DB(\d+)\.DB([XBWD])(\d+)(?:\.([0-7]))?',a)
    if m: db,size,offset,bit=m.groups(); area=0x84; db=int(db)
    else:
        m=re.fullmatch(r'([MIQ])([BWD]?)(\d+)(?:\.([0-7]))?',a)
        if not m: raise ValueError('Invalid address: '+a)
        ar,size,offset,bit=m.groups(); area={'M':0x83,'I':0x81,'Q':0x82}[ar]; db=0; size=size or 'X'
    typ=t['type']; expected={'BOOL':'X','BYTE':'B','INT':'W','UINT':'W','DINT':'D','UDINT':'D','REAL':'D'}[typ]
    if size!=expected or (bit is not None)!=(typ=='BOOL'): raise ValueError('Address/type mismatch')
    offset=int(offset); length=struct.calcsize('>'+FORMATS[typ]);
    if db>65535 or offset+length>65536: raise ValueError('Address out of range')
    return area,db,offset*8+int(bit) if bit is not None else offset,length,bit is not None
class Driver:
    def __init__(self,c):
        if os.name!='nt' or C.sizeof(C.c_void_p)!=4: raise RuntimeError('Real PLC requires Windows Python 32-bit')
        self.h=-1; self.di=self.dc=None; self.ready=False; self.initialized=False
        try: self.s7online=C.WinDLL('S7onlinx.dll',winmode=0)
        except OSError as exc: raise RuntimeError('S7onlinx.dll tidak ditemukan oleh Python x86. Periksa instalasi STEP 7 / S7ONLINE.') from exc
        self.dll_dir=os.add_dll_directory(str(ROOT)); self.lib=C.WinDLL(str(ROOT/'libnodave.dll')); P=C.c_void_p; I=C.c_int; S=C.c_char_p
        class FDS(C.Structure): _fields_=[('rfd',I),('wfd',I)]
        self.fds=FDS
        for n,r,args in [('openS7online',I,[S,P]),('closeS7online',None,[I]),('daveNewInterface',P,[FDS,S,I,I,I]),('daveNewConnection',P,[P,I,I,I]),('daveSetTimeout',None,[P,I]),('daveInitAdapter',I,[P]),('daveConnectPLC',I,[P]),('daveDisconnectPLC',I,[P]),('daveDisconnectAdapter',I,[P]),('daveFree',None,[P])]+[(n,I,[P,I,I,I,I,P]) for n in ['daveReadBytes','daveReadBits','daveWriteBytes','daveWriteBits']]:
            f=getattr(self.lib,n); f.restype=r; f.argtypes=args
        try:
            self.h=self.lib.openS7online(c['access'].encode('ascii'),None)
            if self.h<0: raise RuntimeError('openS7online failed')
            self.di=self.lib.daveNewInterface(FDS(self.h,self.h),b'S7ONLINE',int(c['local']),50,2)
            if not self.di: raise RuntimeError('Interface allocation failed')
            self.lib.daveSetTimeout(self.di,int(c['timeout'])*1000)
            self.check(self.lib.daveInitAdapter(self.di)); self.initialized=True; self.dc=self.lib.daveNewConnection(self.di,int(c['remote']),0,2)
            if not self.dc: raise RuntimeError('Connection allocation failed')
            self.check(self.lib.daveConnectPLC(self.dc)); self.ready=True
        except Exception: self.close(); raise
    def check(self,rc):
        if rc: raise RuntimeError(f'Libnodave error {rc}')
    def read(self,t):
        a,d,o,n,b=address(t); buf=C.create_string_buffer(n); self.check((self.lib.daveReadBits if b else self.lib.daveReadBytes)(self.dc,a,d,o,n,buf)); return int(bool(buf.raw[0])) if b else struct.unpack('>'+FORMATS[t['type']],buf.raw)[0]
    def write(self,t,raw):
        a,d,o,n,b=address(t); buf=C.create_string_buffer(struct.pack('>'+FORMATS[t['type']],raw),n); self.check((self.lib.daveWriteBits if b else self.lib.daveWriteBytes)(self.dc,a,d,o,n,buf))
    def close(self):
        if self.dc:
            if self.ready: self.lib.daveDisconnectPLC(self.dc)
            self.lib.daveFree(self.dc); self.dc=None
        if self.di:
            if self.initialized: self.lib.daveDisconnectAdapter(self.di)
            self.lib.daveFree(self.di); self.di=None
        if self.h>=0: self.lib.closeS7online(self.h); self.h=-1
        self.dll_dir.close()
class Demo:
    def __init__(self,c): self.values={}
    def read(self,t): return self.values.get((t['address'],t['type']),0 if t['type']=='BOOL' else 10)
    def write(self,t,v): self.values[(t['address'],t['type'])]=v
    def close(self): pass

# Native Windows calls may block or crash independently of Python. Keep them
# in a spawned process, never in the dashboard process. One request at a time.
def _driver_process(pipe,c):
    d=None
    try:
        d=Driver(c); pipe.send((True,None))
        while True:
            name,args=pipe.recv()
            if name=='close':
                d.close(); d=None; pipe.send((True,None)); break
            try: pipe.send((True,getattr(d,name)(*args)))
            except Exception as e: pipe.send((False,str(e)))
    except (EOFError,BrokenPipeError): pass
    except Exception as e:
        try: pipe.send((False,str(e)))
        except Exception: pass
    finally:
        if d:
            try: d.close()
            except Exception: pass
        pipe.close()

class IsolatedDriver:
    def __init__(self,c):
        import multiprocessing as mp
        if os.name!='nt' or C.sizeof(C.c_void_p)!=4: raise RuntimeError('Real PLC requires Windows Python 32-bit')
        if not (ROOT/'libnodave.dll').is_file(): raise RuntimeError('Copy libnodave.dll 32-bit from working gateway first')
        self.timeout=max(5,c['timeout']/1000+5); ctx=mp.get_context('spawn')
        self.pipe,other=ctx.Pipe(); self.proc=ctx.Process(target=_driver_process,args=(other,c),daemon=True)
        try:
            self.proc.start(); other.close(); self.receive(max(15,self.timeout*2))
        except Exception:
            other.close(); self.kill(); raise
    def kill(self):
        try: self.pipe.close()
        except Exception: pass
        if self.proc.pid is not None:
            if self.proc.is_alive(): self.proc.terminate()
            self.proc.join(2)
            if self.proc.is_alive(): self.proc.kill(); self.proc.join(2)
    def receive(self,timeout):
        if not self.pipe.poll(timeout): self.kill(); raise RuntimeError('Native driver deadline exceeded; process stopped')
        try: ok,result=self.pipe.recv()
        except (OSError,EOFError) as e: self.kill(); raise RuntimeError('Native driver exited; check DLL/adapter') from e
        if not ok: raise RuntimeError(result)
        return result
    def call(self,name,*args):
        if not self.proc.is_alive(): raise RuntimeError('Native driver process offline')
        try: self.pipe.send((name,args)); return self.receive(self.timeout)
        except (EOFError,OSError) as e: self.kill(); raise RuntimeError('Native driver process offline') from e
    def read(self,t): return self.call('read',t)
    def write(self,t,v): return self.call('write',t,v)
    def close(self):
        try:
            if self.proc.is_alive(): self.call('close')
        finally: self.kill()
