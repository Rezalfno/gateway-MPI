"""Run once on Mini PC. Generates unique credentials, never stores plaintext password."""
import hashlib, ipaddress, json, os, secrets, shutil, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def main():
    print('REMOTE FRONTEND SETUP - use a reserved/static office LAN IP.')
    ip=str(ipaddress.IPv4Address(input('Mini PC Wi-Fi/LAN IPv4: ').strip()))
    if ip=='0.0.0.0' or ipaddress.IPv4Address(ip).is_loopback:raise ValueError('Use actual LAN IPv4')
    network=ipaddress.IPv4Network(input('Office laptop IPv4 (recommended) or approved /24 subnet: ').strip(),strict=False)
    if network.prefixlen<24:raise ValueError('Use /24 or narrower')
    mode=input('HTTPS (recommended) [Enter] / approved isolated HTTP LAN [type HTTP]: ').strip()
    if mode not in ('','HTTP'):raise ValueError('Choose Enter or HTTP')
    tls=mode!='HTTP'
    if tls:
        exe=shutil.which('openssl')
        if not exe:
            for p in (Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/usr/bin/openssl.exe',Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Git/usr/bin/openssl.exe',Path(os.environ.get('LOCALAPPDATA','C:/'))/'Programs/Git/usr/bin/openssl.exe'):
                if p.is_file():exe=str(p);break
        if not exe:raise RuntimeError('OpenSSL not found. Use Git for Windows with OpenSSL or ask IT for a certificate. HTTP only on explicitly approved isolated LAN.')
        subprocess.run([exe,'req','-x509','-newkey','rsa:3072','-sha256','-nodes','-days','365','-keyout',str(ROOT/'remote_key.pem'),'-out',str(ROOT/'remote_cert.pem'),'-subj','/CN='+ip,'-addext','subjectAltName=IP:'+ip,'-addext','extendedKeyUsage=serverAuth'],check=True)
        subprocess.run([exe,'x509','-in',str(ROOT/'remote_cert.pem'),'-outform','DER','-out',str(ROOT/'remote_cert.cer')],check=True)
        print('Certificate fingerprint SHA256:',hashlib.sha256((ROOT/'remote_cert.cer').read_bytes()).hexdigest())
    password=secrets.token_urlsafe(24)
    config=dict(bind_ip=ip,port=8766,allowed_network=str(network),username='gateway-admin',password_sha256=hashlib.sha256(password.encode()).hexdigest(),tls=tls,allow_insecure_http=not tls)
    tmp=ROOT/'remote_config.tmp';tmp.write_text(json.dumps(config,indent=2),encoding='utf8');os.replace(tmp,ROOT/'remote_config.json')
    print('\nURL:',('https' if tls else 'http')+'://'+ip+':8766')
    print('Username:',config['username'],'\nPassword:',password,'\nSave password securely; only shown now. Re-run setup to rotate.')
    if tls:print('Copy ONLY remote_cert.cer to office laptop and verify fingerprint with IT before trusting it. Never share remote_key.pem.')
    else:print('WARNING: HTTP credentials and data are not encrypted. Never use on public/shared/untrusted networks.')
    print('Next run setup_firewall.bat as administrator, then restart gateway.')
if __name__=='__main__':main()
