# Industrial Gateway V7 — PT SMM

Windows 10/11, Python 3.11 x86 (32-bit), frontend lokal + remote LAN dan backend multi-controller.

## Setup
1. Extract folder baru. Stop gateway V5.1 yang masih memakai port 8765.
2. Copy `libnodave.dll` x86 yang sudah bekerja ke folder ini bila akan memakai MPI/DP real. STEP 7 / S7ONLINE tetap diperlukan untuk metode tersebut.
3. Edit `mqtt_config.py` **di folder baru** dengan broker milik Anda; jangan commit kredensial. `MQTT_PUBLISH_SIMULATION=True` mengirim ke `pt_smm/simulation/<asset_id>`; produksi ke `pt_smm/telemetry/<asset_id>`. Akun broker perlu ACL yang sesuai.
4. Jalankan `install.bat` (wajib sekali untuk V6, ada dependency baru pymodbus dan pyserial). Installer memeriksa Python x86 dan menjalankan unit test. Jika error, baca CMD sebelum melanjutkan.
5. Jalankan `start.bat`, buka http://127.0.0.1:8765. Jangan buka index.html langsung. Akses lokal tetap tersedia. Akses remote bersifat opt-in, lihat panduan di bawah.

## V7 — Auto-start dan remote office

### Migrasi V6 (sekali saja)
- Stop V6 dan pastikan port 8765 tidak dipakai. Extract V7 ke lokasi permanen, misalnya `C:\SMM\Gateway` (jangan pindahkan folder setelah mendaftarkan task). Jika Windows memblokir script, gunakan Properties > Unblock pada ZIP yang dipercaya sebelum extract; jangan mengubah execution policy global.
- Copy `config_v6.json` dari V6 bila sudah punya mapping, `mqtt_config.py` milik sendiri, dan DLL x86. Nama config tetap sama agar kompatibel. Jangan copy log atau key dari gateway lain. Jalankan `install.bat` di akun Windows yang akan menjalankan gateway. Jalankan `start.bat` untuk commissioning simulasi dulu.
- Connect controller yang ingin dipolling otomatis. V7 menyimpan daftar `resume_controllers` di config_v6.json; Disconnect menghapus pilihan resume. Controller lama dari V6 tidak auto-connect sebelum di-Connect sekali di V7. Edit tag tetap live. Shutdown/reboot normal tidak menghapus pilihan resume. Jika hasil write tidak pasti, controller dihapus dari daftar resume bila config dapat ditulis; tetap verifikasi kondisi mesin sebelum Connect kembali. Tidak ada replay write saat reboot.

### Akses dari Wi-Fi kantor
1. Mini PC dan laptop office harus dapat saling mengakses pada jaringan yang disetujui IT/OT (tidak harus SSID sama jika routing diizinkan). Jalankan `ipconfig` di kedua perangkat. Ambil IPv4 interface **kantor**, bukan IP Ethernet PLC. Minta DHCP reservation untuk kedua IP, atau subnet /24 yang disetujui. Jangan bridge jaringan OT dan Wi-Fi; tidak perlu port forwarding internet.
2. Di Mini PC, double-click `setup_remote.bat`. Isi IPv4 Mini PC, IPv4 laptop office (misalnya `192.168.10.25`) atau CIDR yang disetujui (misalnya `192.168.10.0/24`). HTTPS adalah default. Script mencari OpenSSL (termasuk Git for Windows). Bila tidak tersedia, minta IT memasang OpenSSL / Git for Windows, atau siapkan PEM sertifikat dan private key dari IT. Pilihan `HTTP` hanya untuk LAN terisolasi yang secara eksplisit disetujui IT; password dan data tidak terenkripsi. Jangan gunakan HTTP pada Wi-Fi publik/shared yang tidak tepercaya.
3. Simpan username `gateway-admin` dan password acak yang muncul; password plaintext tidak disimpan di config. Re-run setup untuk rotasi password (dan sertifikat jika HTTPS). HTTPS menghasilkan `remote_cert.cer` dan fingerprint SHA256; transfer **hanya sertifikat publik** ke laptop office melalui jalur tepercaya. Verifikasi fingerprint dengan IT sebelum mempercayai sertifikat pada Windows (Current User Trusted Root Certification Authorities bila disetujui IT), atau gunakan sertifikat CA perusahaan. Jangan bypass certificate warnings. Sertifikat berlaku 365 hari; regenerasi jika kedaluwarsa/IP berubah. Jangan salin private key. Batasi akses folder ke akun gateway dan administrator.
4. Klik kanan `setup_firewall.bat` > Run as administrator. Rule hanya membuka TCP 8766 ke IP interface kantor dan IP/subnet yang dipilih, profil Private/Domain, program Python x86. Jangan menonaktifkan firewall atau mengganti profil tanpa persetujuan IT. Jika PowerShell policy organisasi melarang script, hubungi IT.
5. Restart gateway. Buka di laptop office URL yang dicetak, misalnya `https://192.168.10.50:8766`, login menggunakan kredensial yang dibuat. Frontend lokal tetap `http://127.0.0.1:8765`. Backend memeriksa IP allowlist, Host, Basic authentication, Origin untuk POST, dan melarang remote real WRITE serta perubahan tag RW fisik. Remote READ tag management, live data, dan controller management tersedia. Simulasi write tetap tersedia. Tidak ada MQTT credentials di frontend. Basic auth browser bisa tersimpan sampai sesi browser ditutup; gunakan perangkat tepercaya. Untuk menonaktifkan akses remote, stop gateway, hapus `remote_config.json`, hapus firewall rule `SMM-IndustrialGateway-LAN` (PowerShell admin `Remove-NetFirewallRule -Name SMM-IndustrialGateway-LAN`), lalu start ulang.
6. Bila gagal: cek IP dan profile jaringan (`Get-NetConnectionProfile`), `Test-NetConnection <IP-mini-pc> -Port 8766` dari office, Event Log, `gateway_boot.log`. Client isolation/VLAN/ACL dapat menghalangi koneksi walaupun SSID sama. Listener LAN retry setiap 10 detik jika IP belum tersedia; polling lokal tetap jalan. Tidak ada akses internet yang dibuka.

### Auto-start tanpa login Windows
1. Setelah konfigurasi teruji, stop `start.bat` via Ctrl+C. Klik kanan `setup_autostart.bat` > Run as administrator **dengan akun Windows yang sama** dengan akun STEP7/S7ONLINE yang sudah terbukti bekerja. Masukkan password akun Windows (bukan PIN). Task Scheduler menyimpan kredensial melalui mekanisme Windows, script tidak menulis password ke file. Jika akun berbeda/tiada password, minta IT menyiapkan akun yang sesuai.
2. Task `SMM Industrial Gateway V7` menggunakan path Python 3.11 x86 absolut, trigger At startup, working directory folder gateway, tanpa batas durasi, dan retry setelah kegagalan (1 menit). Proses menunggu 20 detik agar Windows siap. Tidak perlu login jika account/password dan driver mendukung sesi non-interaktif. `--boot` menonaktifkan WRITE fisik walaupun env sebelumnya aktif. MQTT tetap membaca mqtt_config.py / environment milik akun task.
3. Pilih `y` untuk start task sekarang, atau reboot. Setelah reboot, uji dari office **sebelum login Windows**. Lihat Task Scheduler > Task Scheduler Library > SMM Industrial Gateway V7 dan log `gateway_boot.log` (rotasi 2 MB, 3 backup). Pastikan status polling, tag quality, dan MQTT benar, bukan hanya HTTP online. Jika real S7ONLINE gagal hanya pada sesi tanpa login, verifikasi dengan akun yang sama dalam sesi interaktif; gunakan task At logon sebagai fallback jika driver membutuhkan desktop (tidak memenuhi boot tanpa login; jangan memakai auto-login tanpa persetujuan IT). Uji fisik wajib sebelum commissioning.
4. Untuk maintenance, stop/disable task di Task Scheduler sebelum menjalankan `start.bat` manual. Aktifkan kembali setelah selesai. Jangan jalankan dua gateway sekaligus. Jika perlu hapus task, gunakan PowerShell admin `Unregister-ScheduledTask -TaskName 'SMM Industrial Gateway V7' -Confirm:$false`. Jika password Windows berubah, register ulang task. Pastikan sleep/hibernate tidak aktif saat Mini PC diberi listrik; pengaturan BIOS Restore on AC Power Loss terpisah jika ingin otomatis hidup setelah listrik kembali.

## Alur kerja
- Controllers → Tambah controller → pilih MPI, PROFIBUS DP, Modbus RTU atau Modbus TCP. Pilih Simulation untuk percobaan. Simpan, Connect. Masing-masing controller memiliki mode, status, interval, timeout dan sesi sendiri. Ubah konfigurasi fisik setelah Disconnect controller terkait; controller lain tetap berjalan.
- Tag Management → Tambah tag → Source controller → Asset ID, tag code, address, type, scale, access. Add/Edit/Delete/Enable/Disable dapat dilakukan saat polling. Perubahan berlaku untuk siklus berikutnya; hasil pembacaan lama pada tag yang diubah/dihapus tidak dimasukkan kembali. Tag yang sedang menjalankan write tidak dapat diedit. Asset ID dan tag code bersama harus unik di seluruh gateway.
- Dashboard: live value, quality, timestamp, controller filter, write jika diizinkan. Event Log menampilkan hasil readback atau error. Status MQTT dan jumlah batch yang gagal diantrekan tampil sebagai indikator (tanpa pengaturan MQTT di UI). Tidak ada buffering persisten ketika broker putus; data hilang dihitung sebagai dropped batch.
- Config disimpan ke `config_v6.json` secara atomik. Untuk migrasi V5.1, copy `config.json` lama (jangan rename) ke folder V6 sebelum start pertama; akan dimigrasikan bila belum ada config_v6.json. Verifikasi mapping, timeout, mode dan tag sebelum koneksi. Jika file invalid, startup berhenti dengan error dan file lama tidak diubah. Jangan copy konfigurasi contoh langsung ke produksi.

## Protokol dan batasan hardware
| Protokol | Parameter |
|---|---|
| MPI | S7ONLINE, local station, target station, baud |
| PROFIBUS DP | S7ONLINE, local station, target station, baud |
| Modbus RTU | COM, baud, parity N/E/O, stop bits 1/2, unit ID 1–247; 8 data bits |
| Modbus TCP | host, port, unit ID 1–255 |

PG/PC Interface Windows menentukan pengaturan fisik MPI/DP; UI tidak mengubah driver. Rack/slot tidak tampil. Backend mempertahankan argumen internal Libnodave dari V5.1. Demi mencegah konflik USB/S7ONLINE, hanya **satu sesi S7 fisik** (MPI atau DP) boleh aktif per instance V6. Controller lain boleh tetap aktif melalui Modbus. Beberapa controller simulasi S7 boleh aktif bersamaan. Dukungan beberapa station S7 fisik sekaligus belum divalidasi dan belum diaktifkan.

Modbus RTU: slave dengan COM yang sama harus memiliki baud, parity, stop bits, timeout yang sama dan unit ID berbeda. Satu client serial dengan lock per COM menjalankan request bergantian. Jangan menambah master RTU ke bus yang sudah dikendalikan PLC/HMI tanpa desain jaringan yang sesuai. Gunakan port RS485 yang memang tersedia untuk polling dan periksa manual VFD, parameter komunikasi dan wiring. Modbus TCP memakai sesi per controller; pastikan perangkat mendukung jumlah koneksi.

## Mapping
S7: DB3.DBX0.0 (BOOL), DB3.DBB0 (BYTE), DB3.DBW2 (INT/UINT), DB3.DBD4 (REAL/DINT/UDINT), M/I/Q sesuai alamat. Contoh bukan mapping mesin aktual.
Modbus: address harus **offset PDU zero-based** (bukan referensi 40001). Ikuti manual vendor; contoh 40001 lazimnya offset 0. Area Holding FC03, Input FC04, Coil FC01, Discrete FC02. BOOL hanya Coil/Discrete; register INT/UINT 16 bit atau REAL/DINT/UDINT 32 bit. Byte order ABCD/BADC/CDAB/DCBA; pada 16 bit BADC/DCBA membalik kedua byte. Scale = raw × multiplier. Contoh simulasi ATV312: offset 3202, INT, holding, scale 0.1 (periksa mapping aktual sebelum real).

## Write
Simulation: tag READ / WRITE, aktif, kualitas SIMULATED. Real: default OFF. Aktifkan hanya setelah commissioning resmi, persetujuan operator dan verifikasi register, batas dan interlock. Backend membutuhkan env `MPI_ENABLE_WRITE=1`, tag RW, min/max engineering eksplisit, reading fresh, konfirmasi. S7 real dibatasi DB/M, Modbus real hanya holding/coil. Tidak menerima write melalui MQTT. Write dijalankan sekali oleh worker; hasil readback terlihat di Event Log. Jika mismatch/timeout, worker berhenti polling untuk menghindari pengulangan otomatis; verifikasi kondisi fisik sebelum reconnect. Tidak menggantikan safety interlock PLC/VFD.

## MQTT
Schema sama dengan V5.1: array JSON berisi asset_id, tag_code, source_ts UTC, value_number, value_text, quality, gateway_id, message_id. Batch per controller per Asset ID, sehingga beberapa controller yang memakai asset sama dapat mengirim batch terpisah ke topic yang sama. QoS 1, retain false; subscriber dapat menerima duplikat dan perlu dedup message_id bila diperlukan. Paho auto reconnect, antrean bounded. SIMULATED tidak masuk telemetry produksi. BAD pada pembacaan gagal. MQTT Explorer: subscribe pt_smm/simulation/# atau pt_smm/telemetry/#. Status broker terhubung tidak sama dengan konfirmasi subscriber menerima data; verifikasi dengan Explorer.

## Test dan commissioning
`py -3.11-32 -m unittest -v test_gateway test_remote`
Unit test lokal menggunakan simulasi dan mock Modbus, bukan hardware nyata. Test syntax JavaScript dengan Node hanya dilakukan saat pengembangan (Node tidak diperlukan runtime). V7 diuji melalui HTTP loopback dengan autentikasi, Host/Origin, remote edit dan pemulihan konfigurasi; browser end-to-end, Windows Task Scheduler dan firewall belum diuji di Windows. Real MPI/DP, RS485, Modbus TCP dan broker asli harus diuji terpisah di Windows. Mulai dengan 1 tag READ per controller; cek live quality, scan time, event log, MQTT; tambah tag bertahap. Polling interval merupakan minimum waktu antar awal scan bila scan lebih lambat; bukan jaminan tepat waktu real-time.

Dependencies pinned: Paho MQTT 2.1.0, PyModbus 3.8.6, pyserial 3.5. API Modbus: https://pymodbus.readthedocs.io/en/v3.8.6/source/client.html
