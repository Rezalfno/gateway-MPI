"""Modbus RTU/TCP (pymodbus 3.8.6); one shared client per active serial bus."""
import struct, threading
FORMATS={'INT':'h','UINT':'H','DINT':'i','UDINT':'I','REAL':'f'}
ORDERS={'ABCD':(0,1,2,3),'BADC':(1,0,3,2),'CDAB':(2,3,0,1),'DCBA':(3,2,1,0)}
READ={'holding':'read_holding_registers','input':'read_input_registers','coil':'read_coils','discrete':'read_discrete_inputs'}

def count(t): return 1 if t['type']=='BOOL' else struct.calcsize('>'+FORMATS[t['type']])//2

def decode(t,registers):
    raw=struct.pack('>'+'H'*len(registers),*registers)
    if len(raw)==4: raw=bytes(raw[i] for i in ORDERS[t['order']])
    elif t['order'] in ('BADC','DCBA'): raw=raw[::-1]
    return struct.unpack('>'+FORMATS[t['type']],raw)[0]

def encode(t,v):
    raw=struct.pack('>'+FORMATS[t['type']],v)
    if len(raw)==4: raw=bytes(raw[i] for i in ORDERS[t['order']])
    elif t['order'] in ('BADC','DCBA'): raw=raw[::-1]
    return list(struct.unpack('>'+'H'*(len(raw)//2),raw))

class Bus:
    def __init__(self,client): self.client=client; self.lock=threading.Lock(); self.refs=0
BUSES={}; BUS_LOCK=threading.Lock()

def serial_signature(c): return tuple(c[k] for k in ('baudrate','parity','stopbits','timeout'))

class ModbusDriver:
    def __init__(self,c):
        from pymodbus.client import ModbusTcpClient, ModbusSerialClient
        self.c=c; self.key=None; self.bus=None
        if c['transport']=='Modbus RTU':
            key=c['port_name'].upper(); sig=serial_signature(c)
            with BUS_LOCK:
                if key in BUSES:
                    s,b=BUSES[key]
                    if s!=sig: raise ValueError('Serial bus settings conflict')
                else:
                    b=Bus(ModbusSerialClient(port=key,baudrate=c['baudrate'],bytesize=8,parity=c['parity'],stopbits=c['stopbits'],timeout=c['timeout']/1000,retries=0)); BUSES[key]=(sig,b)
                b.refs+=1; self.key=key; self.bus=b
        else: self.bus=Bus(ModbusTcpClient(c['host'],port=c['tcp_port'],timeout=c['timeout']/1000,retries=0))
        try:
            with self.bus.lock:
                if not self.bus.client.connected and not self.bus.client.connect(): raise ConnectionError('Modbus connection failed')
        except Exception: self.close(); raise
    def call(self,name,*args,**kwargs):
        with self.bus.lock:
            cl=self.bus.client
            if not cl.connected and not cl.connect(): raise ConnectionError('Modbus connection failed')
            r=getattr(cl,name)(*args,slave=self.c['unit_id'],**kwargs)
            if r is None or r.isError(): raise RuntimeError('Modbus: '+str(r))
            return r
    def read(self,t):
        n=count(t); r=self.call(READ[t['area']],int(t['address']),count=n)
        if t['type']=='BOOL':
            if not r.bits: raise RuntimeError('Empty bit response')
            return int(r.bits[0])
        if len(r.registers)!=n: raise RuntimeError('Unexpected register count')
        return decode(t,r.registers)
    def write(self,t,v):
        if t['area']=='coil': self.call('write_coil',int(t['address']),bool(v))
        elif t['area']=='holding':
            words=encode(t,v); self.call('write_register' if len(words)==1 else 'write_registers',int(t['address']),words[0] if len(words)==1 else words)
        else: raise ValueError('Read-only Modbus area')
    def close(self):
        b,self.bus=self.bus,None
        if not b:return
        if self.key:
            with BUS_LOCK:
                b.refs-=1
                if b.refs==0:
                    with b.lock: b.client.close()
                    BUSES.pop(self.key,None)
        else:
            with b.lock: b.client.close()
