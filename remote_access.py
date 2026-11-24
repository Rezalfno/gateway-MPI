"""Opt-in LAN listener. Local listener is unaffected by LAN failures."""
import base64, hashlib, hmac, ipaddress, json, ssl, threading, time
from pathlib import Path

class RemotePolicy:
    def __init__(self, config):
        self.ip=str(ipaddress.IPv4Address(config['bind_ip']))
        self.port=int(config.get('port',8766))
        if not 1024<=self.port<=65535 or self.port==8765: raise ValueError('Remote port invalid')
        if self.ip=='0.0.0.0': raise ValueError('Use one specific LAN IP')
        self.network=ipaddress.IPv4Network(config['allowed_network'],strict=False)
        if self.network.prefixlen<24: raise ValueError('Allowlist must be /24 or narrower')
        self.username=config['username']; self.digest=config['password_sha256']
        if not self.username or ':' in self.username or len(self.digest)!=64: raise ValueError('Invalid remote credentials')
        self.tls=config.get('tls',True) is True
        if not self.tls and config.get('allow_insecure_http') is not True: raise ValueError('HTTP requires explicit opt-in')
        self.cert=config.get('cert','remote_cert.pem'); self.key=config.get('key','remote_key.pem')
    @property
    def host(self):return f'{self.ip}:{self.port}'
    @property
    def origin(self):return ('https://' if self.tls else 'http://')+self.host
    def permits(self,ip,host):
        try:return ipaddress.IPv4Address(ip) in self.network and host==self.host
        except ValueError:return False
    def authenticated(self,header):
        try:
            scheme,encoded=header.split(' ',1)
            if scheme.lower()!='basic':return False
            user,password=base64.b64decode(encoded,validate=True).decode('utf8').split(':',1)
            return hmac.compare_digest(user.encode(),self.username.encode()) and hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(),self.digest)
        except (ValueError,UnicodeError,AttributeError):return False
    def context(self,root):
        if not self.tls:return None
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(root/self.cert),str(root/self.key));return ctx

def start_remote(root,server_class,handler,gateway):
    path=root/'remote_config.json'
    if not path.exists():return None
    try:
        policy=RemotePolicy(json.loads(path.read_text(encoding='utf-8-sig')))
        context=policy.context(root)
    except Exception as e:
        gateway.log('Remote disabled: '+str(e));return None
    stop=threading.Event(); holder={}; lock=threading.Lock()
    def run():
        while not stop.is_set():
            server=None
            try:
                server=server_class((policy.ip,policy.port),handler);server.gateway=gateway;server.remote_policy=policy;server.tls_context=context
                with lock:holder['server']=server
                gateway.log('Remote frontend: '+policy.origin+' (restricted LAN)')
                # handle_request has timeout; shutdown works even if startup/stop race occurs.
                server.timeout=.5
                while not stop.is_set():server.handle_request()
            except OSError as e:
                gateway.log('Remote listener unavailable; retry in 10 s: '+str(e));stop.wait(10)
            finally:
                with lock:holder.pop('server',None)
                if server:server.server_close()
    thread=threading.Thread(target=run,daemon=True);thread.start()
    def close():stop.set();thread.join(2)
    return close
