"""
══════════════════════════════════════════════════════════════════
OMAH SABUN — WhatsApp Bot CS + Order + Marketing
PT Naraya Jagad Sejahtera
Fase 4 — powered by Fonnte + Google Sheets + Gemini AI

FITUR:
1. Bot CS Otomatis (menu interaktif + AI Gemini)
2. Order via WhatsApp → simpan ke Google Sheets
3. Blast Marketing ke list pelanggan
4. Notifikasi order baru ke nomor admin
5. Panel Admin (keyword rahasia)
══════════════════════════════════════════════════════════════════
"""

import os
import json
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
import google.generativeai as genai

# ─── CONFIG ────────────────────────────────────────────────────────
FONNTE_TOKEN  = os.environ.get('FONNTE_TOKEN', '')
WEBAPP_URL    = os.environ.get('WEBAPP_URL', '')
WEBAPP_SECRET = os.environ.get('WEBAPP_SECRET', 'omahsabun_naraya_2024')
GEMINI_KEY    = os.environ.get('GEMINI_API_KEY', '')
ADMIN_WA      = os.environ.get('ADMIN_WA', '')          # format: 628xxx (tanpa +)
ADMIN_SECRET  = os.environ.get('ADMIN_SECRET', 'admin dara')  # keyword panel admin

NAMA_TOKO = 'Omah Sabun'
NAMA_PT   = 'PT Naraya Jagad Sejahtera'
ALAMAT    = os.environ.get('ALAMAT_TOKO', 'Hubungi admin untuk info alamat lengkap')
JAM_BUKA  = os.environ.get('JAM_BUKA',   'Senin - Sabtu: 08.00 - 17.00 WIB')
KOTA      = os.environ.get('KOTA',       'Yogyakarta')

SESSION_TIMEOUT = 1800  # 30 menit tanpa aktivitas → reset sesi

# ─── INIT ─────────────────────────────────────────────────────────
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

ai_model = None
if GEMINI_KEY:
    try:
        genai.configure(api_key=GEMINI_KEY)
        # Coba model terbaru dulu, fallback ke versi lama
        for _model_name in ['gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-pro', 'gemini-1.0-pro']:
            try:
                ai_model = genai.GenerativeModel(_model_name)
                # Test quick
                _test = ai_model.generate_content('hi')
                log.info(f'Gemini model aktif: {_model_name}')
                break
            except Exception as _e:
                log.warning(f'Model {_model_name} gagal: {_e}')
                ai_model = None
    except Exception as _e:
        log.error(f'Gemini init error: {_e}')
        ai_model = None

# ─── STATE MANAGEMENT ─────────────────────────────────────────────
# user_sessions[nomor_wa] = { state, data, last_activity }
user_sessions  = {}
sessions_lock  = threading.Lock()

def get_session(sender):
    """Ambil atau buat sesi user. Auto-reset jika sudah timeout."""
    now = datetime.now()
    with sessions_lock:
        sess = user_sessions.get(sender)
        if sess is None or (now - sess['last_activity']).seconds > SESSION_TIMEOUT:
            user_sessions[sender] = {
                'state': 'start',
                'data': {},
                'last_activity': now
            }
        else:
            user_sessions[sender]['last_activity'] = now
        return user_sessions[sender]

def set_state(sender, state, extra_data=None):
    sess = get_session(sender)
    sess['state'] = state
    if extra_data:
        sess['data'].update(extra_data)

def clear_session_data(sender):
    sess = get_session(sender)
    sess['data']  = {}
    sess['state'] = 'menu'

# ─── FONNTE API ───────────────────────────────────────────────────
def send_wa(target, message):
    """Kirim pesan WhatsApp via Fonnte."""
    try:
        r = requests.post(
            'https://api.fonnte.com/send',
            headers={'Authorization': FONNTE_TOKEN},
            data={
                'target':      target,
                'message':     message,
                'countryCode': '62'
            },
            timeout=20
        )
        result = r.json()
        log.info(f'Send WA → {target}: {r.status_code} | {result.get("status","?")}')
        return result
    except Exception as e:
        log.error(f'send_wa error: {e}')
        return None

def format_nomor(nomor):
    """Konversi nomor lokal ke format internasional (tanpa +)."""
    nomor = str(nomor).strip().replace(' ', '').replace('-', '')
    if nomor.startswith('0'):
        nomor = '62' + nomor[1:]
    elif nomor.startswith('+'):
        nomor = nomor[1:]
    return nomor

# ─── GOOGLE SHEETS API ────────────────────────────────────────────
def api_get(action, params=None):
    try:
        p = {'action': action, 'key': WEBAPP_SECRET}
        if params:
            p.update(params)
        r = requests.get(WEBAPP_URL, params=p, timeout=20)
        return r.json()
    except Exception as e:
        log.error(f'api_get [{action}] error: {e}')
        return None

def api_post(payload):
    try:
        payload['key'] = WEBAPP_SECRET
        r = requests.post(WEBAPP_URL, json=payload, timeout=20)
        return r.json()
    except Exception as e:
        log.error(f'api_post [{payload.get("action")}] error: {e}')
        return None

# ─── PRODUK FALLBACK (hardcoded — dipakai jika Google Sheets API gagal) ────
PRODUK_FALLBACK = [
    {'id': 'P001', 'nama': 'DARA Sabun Cuci Piring',      'kategori': 'Sabun Cuci',       'harga_per_ml': 8},
    {'id': 'P002', 'nama': 'DARA Sabun Cuci Baju',         'kategori': 'Sabun Cuci',       'harga_per_ml': 7},
    {'id': 'P003', 'nama': 'DARA Sabun Cuci Buah & Sayur', 'kategori': 'Sabun Cuci',       'harga_per_ml': 11},
    {'id': 'P004', 'nama': 'DARA Sabun Lantai',            'kategori': 'Pembersih Rumah',  'harga_per_ml': 6},
    {'id': 'P005', 'nama': 'DARA Pembersih Dapur',         'kategori': 'Pembersih Rumah',  'harga_per_ml': 9},
    {'id': 'P006', 'nama': 'DARA Pembersih Toilet',        'kategori': 'Pembersih Rumah',  'harga_per_ml': 10},
    {'id': 'P007', 'nama': 'DARA Sabun Mandi',             'kategori': 'Perawatan Diri',   'harga_per_ml': 10},
    {'id': 'P008', 'nama': 'DARA Hand Soap / Sabun Tangan','kategori': 'Perawatan Diri',   'harga_per_ml': 12},
]

# ─── PRODUK CACHE ─────────────────────────────────────────────────
_produk_cache      = None
_produk_cache_time = None
CACHE_TTL          = 300  # 5 menit

def get_produk():
    """Ambil daftar produk dari Google Sheets. Jika gagal, pakai PRODUK_FALLBACK."""
    global _produk_cache, _produk_cache_time
    now = datetime.now()

    # Gunakan cache jika masih segar
    if _produk_cache and _produk_cache_time:
        if (now - _produk_cache_time).seconds < CACHE_TTL:
            return _produk_cache

    # Coba ambil dari API
    try:
        data = api_get('get_produk')
        if data and data.get('status') == 'ok':
            produk = data.get('produk', [])
            if produk:
                _produk_cache      = produk
                _produk_cache_time = now
                log.info(f'Produk dari API: {len(produk)} item')
                return _produk_cache
    except Exception as e:
        log.error(f'get_produk API error: {e}')

    # Fallback ke data hardcoded
    log.warning('get_produk: API gagal/kosong → pakai PRODUK_FALLBACK')
    return PRODUK_FALLBACK

# ─── FORMAT PESAN ─────────────────────────────────────────────────
def fmt_rp(angka):
    """Format angka ke Rp xxx.xxx"""
    try:
        return 'Rp {:,.0f}'.format(float(angka)).replace(',', '.')
    except:
        return 'Rp 0'

def pesan_menu_utama(nama=''):
    sapa = f'Halo *{nama}*! 👋\n\n' if nama else 'Halo! 👋\n\n'
    return (
        f'{sapa}'
        f'Selamat datang di *{NAMA_TOKO}* 🧼\n'
        f'_{NAMA_PT}_\n\n'
        f'Pilih menu:\n\n'
        f'1️⃣ Lihat Produk & Harga\n'
        f'2️⃣ Order Sekarang\n'
        f'3️⃣ Info Toko & Lokasi\n'
        f'4️⃣ Cara Order\n'
        f'5️⃣ Tanya CS\n\n'
        f'Ketik *angka* untuk memilih menu 😊\n'
        f'_(Ketik *0* kapan saja untuk kembali ke menu ini)_'
    )

def pesan_daftar_produk(produk_list):
    """Return (teks, dict{nomor_str → produk})"""
    if not produk_list:
        return '⚠️ Data produk belum tersedia. Silakan coba beberapa saat lagi.', {}

    # Kelompokkan per kategori
    kat_map = {}
    for p in produk_list:
        kat = p.get('kategori', 'Produk')
        kat_map.setdefault(kat, []).append(p)

    msg  = f'📦 *PRODUK {NAMA_TOKO.upper()}*\n'
    msg += '━━━━━━━━━━━━━━━━━━━━\n\n'

    no = 1
    nomor_to_produk = {}
    for kat, prods in kat_map.items():
        msg += f'*{kat}*\n'
        for p in prods:
            h_ml   = p.get('harga_per_ml', 0)
            h_500  = int(h_ml * 500)
            h_1000 = int(h_ml * 1000)
            msg += f'  {no}. {p["nama"]}\n'
            msg += f'     💰 {fmt_rp(h_ml)}/ml\n'
            msg += f'     500ml={fmt_rp(h_500)} | 1L={fmt_rp(h_1000)}\n'
            nomor_to_produk[str(no)] = p
            no += 1
        msg += '\n'

    msg += '━━━━━━━━━━━━━━━━━━━━\n'
    msg += 'Ketik *2* untuk order 🛒\n'
    msg += 'Ketik *0* untuk kembali ke menu'
    return msg, nomor_to_produk

def pesan_info_toko():
    return (
        f'📍 *INFO TOKO*\n'
        f'━━━━━━━━━━━━━━━━━━━━\n\n'
        f'🏪 *{NAMA_TOKO}*\n'
        f'🏢 _{NAMA_PT}_\n\n'
        f'📌 *Alamat:*\n{ALAMAT}\n\n'
        f'🕐 *Jam Buka:*\n{JAM_BUKA}\n\n'
        f'🌆 *Kota:* {KOTA}\n\n'
        f'━━━━━━━━━━━━━━━━━━━━\n'
        f'Ketik *0* untuk kembali ke menu'
    )

def pesan_cara_order():
    return (
        f'📋 *CARA ORDER*\n'
        f'━━━━━━━━━━━━━━━━━━━━\n\n'
        f'*🤖 Order via Bot WhatsApp ini:*\n'
        f'1️⃣ Ketik *2* → pilih produk\n'
        f'2️⃣ Masukkan jumlah (ml)\n'
        f'3️⃣ Isi nama & alamat\n'
        f'4️⃣ Konfirmasi order\n'
        f'5️⃣ Admin akan menghubungi untuk konfirmasi & pembayaran\n\n'
        f'*🏪 Datang Langsung ke Toko:*\n'
        f'📍 {ALAMAT}\n'
        f'🕐 {JAM_BUKA}\n\n'
        f'*ℹ️ Info Penting:*\n'
        f'• Minimal order: *500 ml* per produk\n'
        f'• Pembayaran: Cash / Transfer\n'
        f'• Pengiriman bisa diatur dengan admin\n\n'
        f'━━━━━━━━━━━━━━━━━━━━\n'
        f'Ketik *0* untuk kembali ke menu'
    )

# ─── ADMIN PANEL ──────────────────────────────────────────────────
def pesan_admin_panel():
    """Tampilkan panel admin."""
    now_str = datetime.now().strftime('%d/%m/%Y %H:%M')
    # Coba ambil statistik dari Google Sheets
    stats_msg = ''
    try:
        data = api_get('get_stats')
        if data and data.get('status') == 'ok':
            total_order  = data.get('total_order', 0)
            order_baru   = data.get('order_baru', 0)
            total_omzet  = data.get('total_omzet', 0)
            stats_msg = (
                f'📊 *STATISTIK*\n'
                f'• Total Order  : {total_order}\n'
                f'• Order Baru   : {order_baru}\n'
                f'• Estimasi Omzet: {fmt_rp(total_omzet)}\n\n'
            )
    except Exception:
        pass

    msg  = f'🔐 *PANEL ADMIN — {NAMA_TOKO}*\n'
    msg += f'━━━━━━━━━━━━━━━━━━━━\n'
    msg += f'🕐 {now_str}\n\n'
    msg += stats_msg
    msg += (
        f'⚙️ *PERINTAH ADMIN:*\n\n'
        f'📦 Lihat produk:\nKetik *1* dari menu utama\n\n'
        f'📋 Lihat order terbaru:\nKetik */order*\n\n'
        f'📢 Blast ke pelanggan:\nKetik */blast [pesan]*\n\n'
        f'🔄 Reload cache produk:\nKetik */reload*\n\n'
        f'📱 Total sesi aktif: {len(user_sessions)}\n\n'
        f'━━━━━━━━━━━━━━━━━━━━\n'
        f'Ketik *0* untuk kembali ke menu utama'
    )
    return msg

def handle_admin_command(sender, text_raw):
    """Tangani perintah admin (diawali /)."""
    cmd_lower = text_raw.lower().strip()

    if cmd_lower == '/reload':
        global _produk_cache, _produk_cache_time
        _produk_cache      = None
        _produk_cache_time = None
        send_wa(sender, '✅ Cache produk direset. Data akan dimuat ulang dari API.\n\nKetik *0* untuk menu.')
        return True

    if cmd_lower == '/order':
        try:
            data = api_get('get_orders_recent')
            if data and data.get('status') == 'ok':
                orders = data.get('orders', [])
                if orders:
                    msg = '📋 *ORDER TERBARU*\n━━━━━━━━━━━━━━━━━━━━\n\n'
                    for o in orders[:10]:
                        msg += (
                            f'📌 No: {o.get("no_order","?")}\n'
                            f'👤 {o.get("nama","?")} | {o.get("no_wa","?")}\n'
                            f'🧴 {o.get("produk","?")}\n'
                            f'💰 {fmt_rp(o.get("total_estimasi",0))}\n'
                            f'📅 {o.get("tanggal","?")}\n\n'
                        )
                    send_wa(sender, msg + 'Ketik *0* untuk menu.')
                else:
                    send_wa(sender, '📋 Belum ada order.\n\nKetik *0* untuk menu.')
            else:
                send_wa(sender, '⚠️ Tidak bisa mengambil data order saat ini.\n\nKetik *0* untuk menu.')
        except Exception as e:
            send_wa(sender, f'❌ Error: {e}\n\nKetik *0* untuk menu.')
        return True

    if cmd_lower.startswith('/blast '):
        pesan_blast = text_raw[7:].strip()
        if not pesan_blast:
            send_wa(sender, '❌ Format: */blast [isi pesan]*')
            return True
        try:
            result = api_get('get_pelanggan_wa')
            if result and result.get('status') == 'ok':
                nomor_list = result.get('nomor_list', [])
                if nomor_list:
                    send_wa(sender, f'📢 Memulai blast ke *{len(nomor_list)}* nomor...')
                    berhasil = 0
                    for nomor in nomor_list:
                        r = send_wa(format_nomor(str(nomor)), pesan_blast)
                        if r:
                            berhasil += 1
                    send_wa(sender, f'✅ Blast selesai: *{berhasil}/{len(nomor_list)}* berhasil.\n\nKetik *0* untuk menu.')
                else:
                    send_wa(sender, '⚠️ Tidak ada nomor pelanggan di Sheets.\n\nKetik *0* untuk menu.')
            else:
                send_wa(sender, '⚠️ Gagal ambil data pelanggan dari Sheets.\n\nKetik *0* untuk menu.')
        except Exception as e:
            send_wa(sender, f'❌ Blast error: {e}\n\nKetik *0* untuk menu.')
        return True

    return False  # bukan perintah admin yang dikenal

# ─── ORDER FLOW ───────────────────────────────────────────────────
def mulai_order(sender, session):
    produk_list = get_produk()
    if not produk_list:
        send_wa(sender, '⚠️ Maaf, data produk belum tersedia. Silakan coba beberapa saat lagi.\n\nKetik *0* untuk menu.')
        return

    msg, nomor_map = pesan_daftar_produk(produk_list)
    session['data']['produk_map'] = nomor_map
    session['data']['cart']       = []
    session['state']              = 'order_pilih_produk'
    send_wa(sender, msg + '\n\n*Ketik nomor produk untuk memesan:*')

def handle_order_pilih_produk(sender, session, text):
    produk_map = session['data'].get('produk_map', {})
    if text not in produk_map:
        send_wa(sender, f'❌ Nomor *{text}* tidak ada di daftar.\nKetik nomor produk yang benar, atau *0* untuk menu.')
        return

    p = produk_map[text]
    session['data']['produk_dipilih'] = p
    session['state'] = 'order_input_volume'

    h_ml = p.get('harga_per_ml', 0)
    send_wa(sender,
        f'✅ Produk dipilih:\n'
        f'🧴 *{p["nama"]}*\n'
        f'💰 {fmt_rp(h_ml)}/ml\n\n'
        f'Berapa ml yang ingin dipesan?\n'
        f'_(Min. 500ml — contoh: 500 / 1000 / 2000)_\n\n'
        f'Ketik *0* untuk batal'
    )

def handle_order_input_volume(sender, session, text):
    try:
        vol = int(text.replace('.', '').replace(',', ''))
    except:
        send_wa(sender, '❌ Format tidak valid. Ketik angka saja.\nContoh: *500* atau *1000*\n\nKetik *0* untuk batal.')
        return

    if vol < 500:
        send_wa(sender, f'⚠️ Minimal order *500 ml*. Masukkan jumlah yang benar.\n\nKetik *0* untuk batal.')
        return
    if vol > 20000:
        send_wa(sender, f'⚠️ Maksimal order via bot adalah *20.000 ml*.\nUntuk order lebih besar, hubungi admin langsung.\n\nKetik *0* untuk batal.')
        return

    p      = session['data']['produk_dipilih']
    h_ml   = p.get('harga_per_ml', 0)
    total  = int(h_ml * vol)

    session['data'].setdefault('cart', []).append({
        'id':       p.get('id', ''),
        'nama':     p['nama'],
        'volume':   vol,
        'harga_ml': h_ml,
        'total':    total
    })
    session['state'] = 'order_lanjut_atau_checkout'

    send_wa(sender,
        f'✅ Ditambahkan ke keranjang:\n'
        f'🧴 {p["nama"]} — {vol:,} ml\n'
        f'💰 Estimasi: {fmt_rp(total)}\n\n'
        f'Ingin tambah produk lagi?\n\n'
        f'1️⃣ Ya, tambah produk lain\n'
        f'2️⃣ Lanjut ke pengiriman\n'
        f'0️⃣ Batalkan order'
    )

def handle_order_lanjut_atau_checkout(sender, session, text):
    if text == '1':
        produk_list = get_produk()
        msg, nomor_map = pesan_daftar_produk(produk_list)
        session['data']['produk_map'] = nomor_map
        session['state'] = 'order_pilih_produk'
        send_wa(sender, msg + '\n\n*Ketik nomor produk berikutnya:*')
    elif text == '2':
        session['state'] = 'order_input_nama'
        send_wa(sender, '📝 Masukkan *nama lengkap* Anda:\n_(Ketik 0 untuk batal)_')
    else:
        send_wa(sender, 'Ketik *1* tambah produk, *2* lanjut, atau *0* batalkan.')

def handle_order_input_nama(sender, session, text):
    if len(text) < 2:
        send_wa(sender, '❌ Nama terlalu pendek. Masukkan nama lengkap Anda.')
        return
    session['data']['nama_pelanggan'] = text
    session['state'] = 'order_input_alamat'
    send_wa(sender,
        f'📍 Halo *{text}*!\n\n'
        f'Masukkan *alamat lengkap* pengiriman:\n'
        f'_(termasuk nama jalan, nomor, RT/RW, kelurahan, kecamatan)_\n\n'
        f'Ketik *0* untuk batal'
    )

def handle_order_input_alamat(sender, session, text):
    if len(text) < 5:
        send_wa(sender, '❌ Alamat terlalu singkat. Masukkan alamat lengkap ya.')
        return
    session['data']['alamat'] = text
    session['state'] = 'order_konfirmasi'

    cart  = session['data'].get('cart', [])
    nama  = session['data'].get('nama_pelanggan', '')
    total = sum(i['total'] for i in cart)

    msg  = '🧾 *RINGKASAN ORDER*\n'
    msg += '━━━━━━━━━━━━━━━━━━━━\n\n'
    for item in cart:
        msg += f'🧴 {item["nama"]}\n'
        msg += f'   {item["volume"]:,} ml × {fmt_rp(item["harga_ml"])}/ml\n'
        msg += f'   = *{fmt_rp(item["total"])}*\n\n'
    msg += f'💰 *Total Estimasi: {fmt_rp(total)}*\n\n'
    msg += f'👤 Nama: {nama}\n'
    msg += f'📍 Alamat: {text}\n'
    msg += f'📱 No. WA: {sender}\n\n'
    msg += '━━━━━━━━━━━━━━━━━━━━\n'
    msg += 'Konfirmasi order?\n\n'
    msg += '1️⃣ *Ya, konfirmasi*\n'
    msg += '2️⃣ Ubah pesanan\n'
    msg += '0️⃣ Batalkan'

    send_wa(sender, msg)

def handle_order_konfirmasi(sender, session, text):
    if text == '2':
        session['data']['cart'] = []
        mulai_order(sender, session)
        return

    if text != '1':
        send_wa(sender, 'Ketik *1* untuk konfirmasi, *2* untuk ubah, atau *0* untuk batal.')
        return

    cart      = session['data'].get('cart', [])
    nama      = session['data'].get('nama_pelanggan', '')
    alamat    = session['data'].get('alamat', '')
    total     = sum(i['total'] for i in cart)
    produk_str = '; '.join([f'{i["nama"]} {i["volume"]:,}ml' for i in cart])

    send_wa(sender, '⏳ Sedang memproses order Anda...')

    result = api_post({
        'action':          'add_order_wa',
        'nama':            nama,
        'no_wa':           sender,
        'produk':          produk_str,
        'total_estimasi':  total,
        'alamat':          alamat,
        'cart':            cart
    })

    if result and result.get('status') == 'ok':
        no_order = result.get('no_order', '-')
        send_wa(sender,
            f'✅ *ORDER BERHASIL DITERIMA!*\n\n'
            f'📋 No. Order: *{no_order}*\n'
            f'🧴 Produk: {produk_str}\n'
            f'💰 Total Estimasi: {fmt_rp(total)}\n'
            f'👤 Nama: {nama}\n'
            f'📍 Alamat: {alamat}\n\n'
            f'Admin kami akan menghubungi Anda segera untuk konfirmasi dan informasi pembayaran.\n\n'
            f'Terima kasih telah berbelanja di *{NAMA_TOKO}* 🧼❤️\n\n'
            f'Ketik *0* untuk kembali ke menu'
        )
        if ADMIN_WA:
            send_wa(ADMIN_WA,
                f'🔔 *ORDER BARU WA!*\n\n'
                f'📋 No: *{no_order}*\n'
                f'👤 {nama}\n'
                f'📱 {sender}\n'
                f'📍 {alamat}\n'
                f'🧴 {produk_str}\n'
                f'💰 {fmt_rp(total)}'
            )
    else:
        send_wa(sender,
            '⚠️ Maaf, terjadi kesalahan saat menyimpan order.\n'
            'Silakan hubungi admin langsung atau coba lagi.\n\n'
            'Ketik *0* untuk menu'
        )

    clear_session_data(sender)

# ─── AI CS ────────────────────────────────────────────────────────
def _rule_based_cs(text):
    """Jawaban rule-based untuk pertanyaan umum. Return None jika tidak cocok."""
    q = text.lower()
    produk_list = get_produk()

    if any(w in q for w in ['diantar', 'kirim', 'delivery', 'ongkir', 'antar', 'ekspedisi', 'cod']):
        return (
            f'Bisa banget kak! \U0001f69a\n\n'
            f'Untuk area {KOTA} bisa antar langsung atau COD. '
            f'Kalau di luar kota bisa via ekspedisi ya kak. Ongkir menyesuaikan jarak \U0001f60a\n\n'
            f'Mau langsung order? Ketik *2* ya!\n_(Ketik *0* untuk menu utama)_'
        )

    if any(w in q for w in ['harga', 'berapa', 'price', 'murah', 'mahal', 'cost']):
        if produk_list:
            msg = 'Ini info harga produk DARA kita kak! \U0001f4b0\n\n'
            for p in produk_list[:6]:
                h = p.get('harga_per_ml', 0)
                msg += f'• {p["nama"]}\n  {fmt_rp(h)}/ml | 500ml = {fmt_rp(int(h * 500))}\n\n'
            msg += 'Harga bisa nego untuk order besar ya kak \U0001f60a\n'
            msg += 'Ketik *2* untuk order, atau *0* untuk menu utama.'
            return msg

    if any(w in q for w in ['dimana', 'di mana', 'lokasi', 'alamat', 'toko', 'tempat', 'beli']):
        return (
            f'Toko kita ada di *{KOTA}* kak! \U0001f4cd\n\n'
            f'\U0001f4cc {ALAMAT}\n'
            f'\U0001f550 {JAM_BUKA}\n\n'
            f'Bisa datang langsung atau order via bot ini. Admin kita siap bantu! \U0001f60a\n\n'
            f'_(Ketik *0* untuk menu utama)_'
        )

    if any(w in q for w in ['jam', 'buka', 'tutup', 'operasional', 'hari apa']):
        return (
            f'Jam operasional kita kak:\n\n'
            f'\U0001f550 *{JAM_BUKA}*\n\n'
            f'Di luar jam itu bisa tetap order via bot ini 24 jam ya, '
            f'nanti admin follow up saat jam kerja \U0001f60a\n\n'
            f'_(Ketik *0* untuk menu utama)_'
        )

    if any(w in q for w in ['produk', 'sabun', 'katalog', 'ada apa', 'jual apa', 'tersedia']):
        if produk_list:
            msg = 'Ini produk DARA yang tersedia kak! \U0001f9f4\n\n'
            for p in produk_list:
                msg += f'• {p["nama"]}\n'
            msg += '\nKetik *1* untuk lihat harga lengkap \U0001f60a\nKetik *2* untuk langsung order!\n_(Ketik *0* untuk menu utama)_'
            return msg

    if any(w in q for w in ['bayar', 'pembayaran', 'transfer', 'cash', 'tunai', 'bca', 'mandiri', 'dana', 'gopay', 'ovo']):
        return (
            'Untuk pembayaran kita terima kak:\n\n'
            '\U0001f4b5 *Cash / Tunai*\n'
            '\U0001f3e6 *Transfer Bank*\n'
            '\U0001f4f1 *E-wallet (GoPay, OVO, Dana, dll)*\n\n'
            'Setelah order, admin kita yang akan hubungi untuk konfirmasi pembayaran ya kak \U0001f60a\n\n'
            '_(Ketik *0* untuk menu utama)_'
        )

    if any(w in q for w in ['minimum', 'minimal', 'min order', 'minimum order']):
        return (
            'Minimum order kita:\n\n'
            '\U0001f4e6 *500 ml* per produk ya kak\n\n'
            'Untuk order skala besar / grosir, harga lebih spesial — hubungi admin langsung ya! \U0001f60a\n\n'
            'Ketik *2* untuk order sekarang!\n_(Ketik *0* untuk menu utama)_'
        )

    if any(w in q for w in ['aman', 'bpom', 'halal', 'bahan', 'kandungan', 'formula', 'komposisi']):
        return (
            'Produk DARA kita aman dipakai sehari-hari kak! ✅\n\n'
            '\U0001f9ea Formula khusus — efektif membersihkan\n'
            '\U0001f33f Ramah lingkungan\n'
            '\U0001f3ed Diproduksi oleh PT Naraya Jagad Sejahtera\n\n'
            'Ada pertanyaan lebih detail? Admin kita siap bantu! \U0001f60a\n\n'
            '_(Ketik *0* untuk menu utama)_'
        )

    return None


def handle_ai_cs(sender, text):
    """CS humanis — persona Dara dari Omah Sabun. Gemini AI + rule-based fallback."""
    set_state(sender, 'ai_cs')

    # ── 1. Coba Gemini AI dulu ──
    if ai_model:
        try:
            produk_list = get_produk()
            produk_info = '\n'.join([
                f'- {p["nama"]} ({p.get("kategori", "")}) Rp{p.get("harga_per_ml", 0)}/ml, '
                f'500ml=Rp{int(p.get("harga_per_ml", 0) * 500)}'
                for p in produk_list
            ]) if produk_list else 'Belum ada data produk'

            prompt = (
                f'Kamu adalah Dara, CS dari toko sabun "{NAMA_TOKO}" milik {NAMA_PT} di {KOTA}.\n\n'
                f'Karakter Dara:\n'
                f'- Ramah, hangat, dan asik diajak ngobrol seperti teman\n'
                f'- Bahasa santai ala anak muda Indonesia (pakai "kak", "nih", "yuk", "dong")\n'
                f'- Pakai emoji secukupnya, tidak berlebihan\n'
                f'- Jawaban singkat dan to the point (maksimal 5 kalimat)\n'
                f'- Jujur — kalau tidak tahu, bilang tidak tahu dengan sopan\n'
                f'- Tidak kaku, tidak seperti robot\n\n'
                f'Info toko:\n'
                f'- Alamat: {ALAMAT}\n'
                f'- Jam buka: {JAM_BUKA}\n'
                f'- Kota: {KOTA}\n'
                f'- Pengiriman: area {KOTA} bisa antar/COD, luar kota via ekspedisi\n'
                f'- Pembayaran: cash, transfer bank, e-wallet\n'
                f'- Minimum order: 500 ml per produk\n\n'
                f'Daftar produk:\n{produk_info}\n\n'
                f'Pertanyaan customer: "{text}"\n\n'
                f'Instruksi tambahan:\n'
                f'- Jawab pertanyaan di atas sebagai Dara\n'
                f'- Kalau ada yang mau order, arahkan ke menu *2*\n'
                f'- Akhiri dengan ajakan/pertanyaan balik agar percakapan hidup\n'
                f'- Di akhir SELALU tambahkan: "_(Ketik *0* untuk menu utama)_"'
            )

            generation_config = genai.types.GenerationConfig(temperature=0.85, max_output_tokens=400)
            response = ai_model.generate_content(prompt, generation_config=generation_config)
            reply = response.text.strip() if (response and response.text) else ''

            if reply:
                send_wa(sender, reply)
                return

        except Exception as e:
            log.error(f'Gemini error untuk {sender}: {e}')

    # ── 2. Fallback: rule-based ──
    rule_reply = _rule_based_cs(text)
    if rule_reply:
        send_wa(sender, rule_reply)
        return

    # ── 3. Fallback terakhir ──
    send_wa(sender,
        'Hai kak! \U0001f60a Dara di sini siap bantu.\n\n'
        'Dara kurang ngerti maksud pertanyaannya nih, bisa dijelasin lebih detail?\n\n'
        '\U0001f6d2 *Harga & produk* — ketik "harga"\n'
        '\U0001f69a *Pengiriman* — ketik "diantar"\n'
        '\U0001f4cd *Lokasi toko* — ketik "dimana"\n'
        '\U0001f4b3 *Cara bayar* — ketik "bayar"\n\n'
        'Atau ketik *2* untuk langsung order ya! \U0001f60a\n_(Ketik *0* untuk menu utama)_'
    )

# ─── MAIN HANDLER ─────────────────────────────────────────────────
KEYWORD_RESET = {
    '0', 'menu', 'mulai', 'start', 'halo', 'hai', 'hi', 'hello',
    'helo', 'assalamualaikum', 'permisi', 'selamat pagi',
    'selamat siang', 'selamat sore', 'selamat malam'
}

def handle_message(sender, text, pushname=''):
    text_raw   = text.strip()
    text_lower = text_raw.lower()

    log.info(f'MSG from {sender} [{pushname}]: {text_raw[:80]}')

    # ── Admin panel (keyword rahasia) ──
    if text_lower == ADMIN_SECRET.lower():
        # Siapapun yang tahu keyword bisa akses, tapi idealnya hanya admin
        set_state(sender, 'admin')
        send_wa(sender, pesan_admin_panel())
        return

    # ── Perintah admin (diawali /) ──
    session = get_session(sender)
    if text_raw.startswith('/') and session.get('state') == 'admin':
        handled = handle_admin_command(sender, text_raw)
        if handled:
            return

    # ── Global reset keywords ──
    if text_lower in KEYWORD_RESET or text_raw == '0':
        set_state(sender, 'menu')
        send_wa(sender, pesan_menu_utama(pushname))
        return

    state = session['state']

    # ── start / menu ──
    if state in ('start', 'menu'):
        if text_raw == '1':
            produk_list = get_produk()
            msg, _      = pesan_daftar_produk(produk_list)
            set_state(sender, 'browse_produk')
            send_wa(sender, msg)
        elif text_raw == '2':
            mulai_order(sender, session)
        elif text_raw == '3':
            send_wa(sender, pesan_info_toko())
        elif text_raw == '4':
            send_wa(sender, pesan_cara_order())
        elif text_raw == '5':
            set_state(sender, 'ai_cs')
            send_wa(sender,
                '\U0001f9f4 *Tanya CS Omah Sabun*\n\n'
                'Halo kak! Dara di sini siap bantu \U0001f60a\n\n'
                'Silakan tanyakan apa saja — soal produk, harga, pengiriman, atau info toko.\n\n'
                '_(Ketik *0* untuk kembali ke menu)_'
            )
        else:
            set_state(sender, 'menu')
            send_wa(sender, pesan_menu_utama(pushname))
        return

    # ── browse produk ──
    if state == 'browse_produk':
        if text_raw == '2':
            mulai_order(sender, session)
        else:
            set_state(sender, 'menu')
            send_wa(sender, pesan_menu_utama())
        return

    # ── order flow ──
    if state == 'order_pilih_produk':
        handle_order_pilih_produk(sender, session, text_raw)
        return
    if state == 'order_input_volume':
        handle_order_input_volume(sender, session, text_raw)
        return
    if state == 'order_lanjut_atau_checkout':
        handle_order_lanjut_atau_checkout(sender, session, text_raw)
        return
    if state == 'order_input_nama':
        handle_order_input_nama(sender, session, text_raw)
        return
    if state == 'order_input_alamat':
        handle_order_input_alamat(sender, session, text_raw)
        return
    if state == 'order_konfirmasi':
        handle_order_konfirmasi(sender, session, text_raw)
        return

    # ── AI CS ──
    if state == 'ai_cs':
        handle_ai_cs(sender, text_raw)
        return

    # ── Admin state — perintah / ──
    if state == 'admin':
        if text_raw.startswith('/'):
            handle_admin_command(sender, text_raw)
        else:
            send_wa(sender, pesan_admin_panel())
        return

    # Default fallback
    set_state(sender, 'menu')
    send_wa(sender, pesan_menu_utama(pushname))

# ─── FLASK ROUTES ─────────────────────────────────────────────────
@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint utama — Fonnte mengirim setiap pesan masuk ke sini."""
    try:
        data = request.json or {}
        log.info(f'Webhook: {json.dumps(data)[:200]}')

        sender   = str(data.get('sender', '')).strip()
        message  = str(data.get('message', '')).strip()
        pushname = data.get('pushname') or data.get('name') or ''
        isgroup  = data.get('isgroup', False)

        if isgroup:
            return jsonify({'status': 'ok', 'note': 'group skipped'})

        if not sender or not message:
            return jsonify({'status': 'ok', 'note': 'empty payload'})

        # Proses di background agar webhook return cepat (< 5 detik)
        t = threading.Thread(
            target=handle_message,
            args=(sender, message, pushname),
            daemon=True
        )
        t.start()

        return jsonify({'status': 'ok'})

    except Exception as e:
        log.error(f'Webhook error: {e}')
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/blast', methods=['POST'])
def blast():
    """
    Endpoint blast marketing.
    POST body: { "secret": "...", "pesan": "...", "nomor_list": ["628xxx", ...] }
    Atau: { "secret": "...", "pesan": "...", "ambil_dari_sheets": true }
    """
    try:
        data   = request.json or {}
        secret = data.get('secret', '')
        if secret != WEBAPP_SECRET:
            return jsonify({'status': 'error', 'msg': 'Unauthorized'}), 403

        pesan      = data.get('pesan', '').strip()
        nomor_list = data.get('nomor_list', [])
        dari_sheets = data.get('ambil_dari_sheets', False)

        if not pesan:
            return jsonify({'status': 'error', 'msg': 'Field "pesan" wajib diisi'})

        if dari_sheets:
            result = api_get('get_pelanggan_wa')
            if result and result.get('status') == 'ok':
                nomor_list = result.get('nomor_list', [])

        if not nomor_list:
            return jsonify({'status': 'error', 'msg': 'Tidak ada nomor tujuan'})

        berhasil = 0
        gagal    = 0
        errors   = []

        for nomor in nomor_list:
            nomor_fmt = format_nomor(str(nomor))
            if not nomor_fmt:
                gagal += 1
                continue
            r = send_wa(nomor_fmt, pesan)
            if r:
                berhasil += 1
            else:
                gagal += 1
                errors.append(nomor_fmt)

        log.info(f'Blast selesai: {berhasil} berhasil, {gagal} gagal dari {len(nomor_list)} nomor')
        return jsonify({
            'status':   'ok',
            'berhasil': berhasil,
            'gagal':    gagal,
            'total':    len(nomor_list),
            'errors':   errors[:10]
        })

    except Exception as e:
        log.error(f'Blast error: {e}')
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/ping')
def ping():
    return jsonify({
        'status': 'ok',
        'msg':    f'{NAMA_TOKO} WA Bot aktif ✅',
        'time':   datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    })


# ─── ENTRY POINT ──────────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    log.info(f'Starting {NAMA_TOKO} WA Bot on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
