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

if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')
else:
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
        f'5️⃣ Tanya CS (AI)\n'
        f'6️⃣ Hubungi Admin Langsung\n\n'
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
def handle_ai_cs(sender, text):
    # Pastikan session tetap di state ai_cs selama percakapan ini
    set_state(sender, 'ai_cs')

    if not ai_model:
        send_wa(sender,
            '⚠️ Maaf, layanan AI sedang tidak aktif.\n'
            'Silakan hubungi admin langsung (ketik *6* di menu).\n\n'
            'Ketik *0* untuk menu'
        )
        return

    try:
        produk_list  = get_produk()
        produk_info  = '\n'.join([
            f'- {p["nama"]} (Kat: {p.get("kategori","")}) harga {fmt_rp(p.get("harga_per_ml",0))}/ml'
            for p in produk_list
        ]) if produk_list else 'Data produk tidak tersedia'

        prompt = (
            f'Kamu adalah CS (Customer Service) toko sabun rumah tangga bernama "{NAMA_TOKO}" '
            f'dari {NAMA_PT}.\n\n'
            f'Info toko:\n'
            f'- Alamat: {ALAMAT}\n'
            f'- Jam buka: {JAM_BUKA}\n'
            f'- Kota: {KOTA}\n\n'
            f'Daftar produk:\n{produk_info}\n\n'
            f'Pesan dari customer: "{text}"\n\n'
            f'Instruksi:\n'
            f'- Jawab dengan ramah, singkat (maksimal 4 kalimat)\n'
            f'- Gunakan Bahasa Indonesia yang santai\n'
            f'- Jika pertanyaan tentang harga, berikan estimasi dari data di atas\n'
            f'- Jika tidak bisa menjawab, arahkan ke admin\n'
            f'- Akhiri jawaban dengan: "Ketik *0* untuk menu utama 😊"'
        )

        response = ai_model.generate_content(prompt)
        reply    = response.text.strip() if response.text else ''

        if not reply:
            raise ValueError('Gemini mengembalikan respons kosong')

        send_wa(sender, reply)

    except Exception as e:
        log.error(f'AI CS error: {e}')
        # Session tetap di ai_cs agar user bisa coba lagi
        send_wa(sender,
            '😅 Maaf, saya sedang kesulitan menjawab.\n'
            'Coba tanyakan lagi, atau ketik *6* untuk bicara langsung dengan admin.\n\n'
            '_(Ketik *0* untuk menu utama)_'
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
                '🤖 *Mode Tanya CS*\n\n'
                'Silakan ketik pertanyaan Anda — saya siap membantu!\n\n'
                '_(Ketik *0* untuk kembali ke menu)_'
            )
        elif text_raw == '6':
            wa_admin = ADMIN_WA or 'belum tersedia'
            send_wa(sender,
                f'📱 *Hubungi Admin Omah Sabun:*\n\n'
                f'WhatsApp: wa.me/{wa_admin}\n\n'
                f'Jam operasional:\n{JAM_BUKA}\n\n'
                f'Ketik *0* untuk menu'
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
