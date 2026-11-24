import json, math, re, uuid
from datetime import datetime, timezone
import mqtt_config as mc
def utc(): return datetime.now(timezone.utc).isoformat()
def integer(v,lo,hi,k):
    if isinstance(v,bool) or not re.fullmatch(r'\d+',str(v)) or not lo<=int(v)<=hi: raise ValueError('Invalid '+k)
    return int(v)
def string(v,k,limit=128):
    if not isinstance(v,str) or not v.strip() or len(v)>limit: raise ValueError('Invalid '+k)
    return v.strip()
def payload(t,v,quality,ts):
    return dict(asset_id=t['asset_id'],tag_code=t['tag_code'],source_ts=ts,value_number=v,value_text=None if v is None else format(v,'.12g'),quality=quality,gateway_id=mc.GATEWAY_ID,message_id=str(uuid.uuid4()))

def mqtt_prefix(simulation=False):
    production=mc.TOPIC_PREFIX.rstrip('/')
    if not simulation: return production
    prefix=getattr(mc,'SIMULATION_TOPIC_PREFIX','pt_smm/simulation').rstrip('/')
    # Reject equal or nested topics (in either direction), including wildcards.
    if (not prefix or not production or any(c in prefix for c in ('#','+','\x00'))
        or prefix==production or prefix.startswith(production+'/') or production.startswith(prefix+'/')):
        raise ValueError('Simulation MQTT prefix must be separate from production')
    return prefix

def mqtt_enabled(simulation=False):
    return bool(mc.MQTT_HOST and (not simulation or getattr(mc,'MQTT_PUBLISH_SIMULATION',False) is True))

def group_payload(batch,simulation=False):
    groups={}; prefix=mqtt_prefix(simulation)
    for item in batch: groups.setdefault(prefix+'/'+item['asset_id'],[]).append(item)
    return groups

class Publisher:
    def __init__(self,log): self.client=None; self.log=log; self.dropped=0
    def start(self,simulation=False):
        if not mqtt_enabled(simulation): return
        try:
            prefix=mqtt_prefix(simulation)
            import paho.mqtt.client as mqtt
            c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=mc.GATEWAY_ID+("-sim" if simulation else ""))
            c.on_connect=lambda client,userdata,flags,reason,props: print('MQTT connect:',reason,flush=True)
            c.on_disconnect=lambda client,userdata,flags,reason,props: print('MQTT disconnect:',reason,flush=True)
            c.max_queued_messages_set(100); c.max_inflight_messages_set(10); c.reconnect_delay_set(2,60)
            if mc.MQTT_USERNAME: c.username_pw_set(mc.MQTT_USERNAME,mc.MQTT_PASSWORD)
            if mc.MQTT_TLS: c.tls_set()
            c.connect_async(mc.MQTT_HOST,int(mc.MQTT_PORT),60); c.loop_start(); self.client=c
            self.log("MQTT " + ("simulation" if simulation else "production") + " topic: " + prefix + "/<asset_id>")
        except Exception as e: self.log('MQTT startup: '+str(e))
    def publish(self,batch,simulation=False):
        if not mqtt_enabled(simulation) or not batch: return
        try:
            groups=group_payload(batch,simulation)
        except ValueError as e:
            self.log(str(e)); self.dropped+=1; return
        for topic,items in groups.items():
            if not self.client or not self.client.is_connected(): self.dropped+=1; continue
            try:
                info=self.client.publish(topic,json.dumps(items,allow_nan=False),qos=1,retain=False)
                if info.rc: self.dropped+=1
            except Exception: self.dropped+=1
    def close(self):
        if self.client:
            try: self.client.disconnect(); self.client.loop_stop()
            except Exception as e: self.log('MQTT close: '+str(e))
            self.client=None

