# malzeme_kontrol.py v2 — MM60 Mükerrer Kontrol (cache + düşük puanlı olası adaylar + debug)
# Akıllı fuzzy matching ile yeni malzeme ekleme öncesi dublikasyon tespiti

import sqlite3, os, re, socket, time
from datetime import datetime
from flask import Blueprint, request, jsonify

try:
    import pandas as pd
except ImportError:
    pd = None

malzeme_bp = Blueprint("malzeme", __name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agirlik_hesaplama.db")


# ───────────────────────────────────────────────────────────────────────────
# DB BAĞLANTI + INIT
# ───────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass
    return conn


def init_malzeme_db():
    conn = get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS malzeme_katalog (
                malzeme_no         TEXT PRIMARY KEY,
                kisa_metin_tr      TEXT DEFAULT '',
                kisa_metin_en      TEXT DEFAULT '',
                kisa_metin_ru      TEXT DEFAULT '',
                uzun_metin_tr      TEXT DEFAULT '',
                uzun_metin_en      TEXT DEFAULT '',
                uzun_metin_ru      TEXT DEFAULT '',
                mal_grubu          TEXT DEFAULT '',
                degerleme_sinifi   TEXT DEFAULT '',
                olcu_birimi        TEXT DEFAULT '',
                malzeme_turu       TEXT DEFAULT '',
                siparis_no         TEXT DEFAULT '',
                tokens_tr          TEXT DEFAULT '',
                guncelleme_tarihi  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_malz_kisa_tr  ON malzeme_katalog(kisa_metin_tr);
            CREATE INDEX IF NOT EXISTS ix_malz_mg       ON malzeme_katalog(mal_grubu);

            CREATE TABLE IF NOT EXISTS malzeme_import_log (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                dosya_adi          TEXT,
                satir_sayisi       INTEGER,
                tarih              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pc_adi             TEXT
            );
        """)
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN tokens_tr TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN guncelleme_tarihi TIMESTAMP")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN tip TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN kalinlik REAL")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN en_olcu REAL")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN boy REAL")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN cap REAL")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN profil_tipi TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN profil_olcu REAL")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN kalite TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN standart TEXT DEFAULT ''")
        except: pass
        try: conn.execute("ALTER TABLE malzeme_katalog ADD COLUMN kaplama TEXT DEFAULT ''")
        except: pass
        conn.commit()
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# NORMALİZASYON
# ───────────────────────────────────────────────────────────────────────────

_TR_BUYUK = str.maketrans({'İ':'i','I':'i','Ş':'s','Ğ':'g','Ü':'u','Ö':'o','Ç':'c'})
_TR_KUCUK = str.maketrans({'ı':'i','ş':'s','ğ':'g','ü':'u','ö':'o','ç':'c'})

_KISALTMA = {
    "gal": "galvaniz", "galv": "galvaniz", "galvz": "galvaniz",
    "paslnm": "paslanmaz", "psl": "paslanmaz",
    "krm": "krom", "sm": "siyah",
    "civ": "civata", "som": "somun",
    "rond": "rondela", "rnd": "rondela",
    "yld": "yıldız", "altks": "altıköşe", "altk": "altıköşe",
    "tor": "torbalı", "torb": "torbalı",
    "pls": "plastik", "alm": "alüminyum", "alum": "alüminyum",
}

# ───────────────────────────────────────────────────────────────────────────
# TEKNİK ÖZELLİK ÇIKARMA (V2) — Sac/Profil/Boru/Demir Düz
# ───────────────────────────────────────────────────────────────────────────

_TIP_ANAHTAR = [
    ("sac",       ["sac", "levha"]),
    ("profil",    ["profil", "upe", "ipe", "hea", "heb", "npu", "npi", "ipn"]),
    ("kose_bent", ["kose bent", "kosebent", "esit kenar", "esitkenar"]),
    ("fitting",   ["dirsek", "reduksiyon", "baglanti", "flans", "kapak", "ek parca", "manson"]),
    ("boru",      ["boru"]),
    ("lama",      ["lama"]),
    ("demir_duz", ["demir duz", "yuvarlak demir", "yuvarlak"]),
]

_PROFIL_TIP_RE  = re.compile(r'\b(upe|ipe|hea|heb|npu|npi|ipn)\b')
_PROFIL_OLCU_RE = re.compile(r'\b(?:upe|ipe|hea|heb|npu|npi|ipn)\s*(\d{2,4})\b')
_SAC_RE         = re.compile(r'\b(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)\b')
_CAP_RE         = re.compile(r'(?:ø|çap|cap)\s*(\d+(?:[.,]\d+)?)')
_BORU_OLCU_RE   = re.compile(r'\b(\d+(?:[.,]\d+)?)\s*x\s*(\d+(?:[.,]\d+)?)\b')
_BOY_RE         = re.compile(r'\bl\s*[=:]\s*(\d+(?:[.,]\d+)?)')
_KALITE_RE      = re.compile(r'\b[a-z]\d{3}[a-z0-9]{0,4}\b')
_KIRIL_RE       = re.compile(r'[а-яё]', re.IGNORECASE)
_STANDART_RE    = re.compile(r'\b(en|din|gost|iso|astm|tu|sto|gost r)\s*-?\s*([a-z0-9][a-z0-9\-\.]{2,20})\b')
_KAPLAMA_KELIME = ["galvaniz", "paslanmaz", "siyah", "krom"]


def _sayi(s):
    try: return float(s.replace(",", "."))
    except: return None


def _boyut_temizle(s):
    """normalize()'dan farklı olarak 'x' bağlarını ve 'ø' işaretini korur —
    boyut/çap regex'lerinin çalışabilmesi için."""
    if not s: return ""
    s = str(s).translate(_TR_BUYUK).lower().translate(_TR_KUCUK)
    s = re.sub(r'[×*]', 'x', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def ozellik_cikar(metin):
    """Sac/profil/boru/demir düz metninden yapısal özellik çıkarır."""
    ham = _boyut_temizle(metin)
    norm = normalize(metin)

    tip = "diger"
    for t, kelimeler in _TIP_ANAHTAR:
        if any(k in norm for k in kelimeler):
            tip = t
            break

    ozellik = {
        "tip": tip, "kalinlik": None, "en": None, "boy": None, "cap": None,
        "profil_tipi": None, "profil_olcu": None, "kalite": None,
        "standart": None, "kaplama": None,
    }


    if tip == "sac":
        m = _SAC_RE.search(ham)
        if m:
            ozellik["kalinlik"] = _sayi(m.group(1))
            ozellik["en"]       = _sayi(m.group(2))
            ozellik["boy"]      = _sayi(m.group(3))
    elif tip == "profil":
        pm = _PROFIL_TIP_RE.search(ham)
        if pm: ozellik["profil_tipi"] = pm.group(1).upper()
        om = _PROFIL_OLCU_RE.search(ham)
        if om: ozellik["profil_olcu"] = _sayi(om.group(1))
    elif tip in ("boru", "demir_duz", "lama"):
        cm = _CAP_RE.search(ham)
        if cm:
            ozellik["cap"] = _sayi(cm.group(1))
            # Çap sonrası "Xkalınlık" var mı? (örn. Ø325X8)
            sonrasi = ham[cm.end():]
            xm = re.match(r'\s*x\s*(\d+(?:[.,]\d+)?)', sonrasi)
            if xm: ozellik["kalinlik"] = _sayi(xm.group(1))
        elif tip == "boru":
            # Boru genelde "169X16" (dış çap x et kalınlığı) şeklinde, "çap" kelimesi yazılmaz
            om = _BORU_OLCU_RE.search(ham)
            if om:
                ozellik["cap"] = _sayi(om.group(1))
                ozellik["kalinlik"] = _sayi(om.group(2))
        bm = _BOY_RE.search(ham)
        if bm: ozellik["boy"] = _sayi(bm.group(1))

    km = _KALITE_RE.search(norm)
    if km:
        ozellik["kalite"] = km.group(0).upper()
    else:
        # Avrupa formatı bulunamadıysa, Rus/GOST kalite kodunu dene
        # (Kiril harfi + rakam birlikte geçen token — örn. 09Г2С, 17Г1С, 3СП)
        for t in norm.split():
            if _KIRIL_RE.search(t) and any(c.isdigit() for c in t):
                ozellik["kalite"] = t.upper()
                break

    sm = _STANDART_RE.search(norm)
    if sm: ozellik["standart"] = (sm.group(1) + sm.group(2)).upper()

    for k in _KAPLAMA_KELIME:
        if k in norm:
            ozellik["kaplama"] = k
            break

    return ozellik

def _override_normalize(ov):
    """Kullanıcının düzelttiği özellik dict'ini temizler/tipe çevirir."""
    if not ov: return None
    out = {}
    for k in ("kalinlik", "en", "boy", "cap", "profil_olcu"):
        v = ov.get(k)
        if v in (None, ""):
            out[k] = None
        else:
            try: out[k] = float(str(v).replace(",", "."))
            except: out[k] = None
    for k in ("tip", "profil_tipi", "kalite", "standart", "kaplama"):
        v = ov.get(k)
        v = str(v).strip() if v not in (None, "") else None
        out[k] = v.upper() if (v and k in ("profil_tipi", "kalite", "standart")) else v
    if not out.get("tip"):
        out["tip"] = "diger"
    return out

@malzeme_bp.route("/api/malzeme/ozellik-onizleme", methods=["POST"])
def malzeme_ozellik_onizleme():
    """Katalog taramadan, sadece metinden hızlıca özellik çıkarır (canlı önizleme için)."""
    d = request.get_json(silent=True) or {}
    metin = (d.get("metin") or "").strip()
    if not metin or len(metin) < 3:
        return jsonify({"durum": "ok", "ozellikler": None})
    return jsonify({"durum": "ok", "ozellikler": ozellik_cikar(metin)})

def _ozellik_puan(qf, cf):
    """İki özellik dict'i arasında 0-100 arası eşleşme puanı."""
    if qf["tip"] == "diger" or cf["tip"] == "diger":
        return None  # tip belirlenemeyen kayıtlarda özellik puanı hesaplanmaz
    if qf["tip"] != cf["tip"]:
        return 0  # farklı tip → kesin uyuşmazlık

    puan, agirlik_toplam = 0, 0

    def karsilastir(qv, cv, agirlik, tolerans=0.02):
        nonlocal puan, agirlik_toplam
        if qv is None or cv is None:
            return
        agirlik_toplam += agirlik
        if isinstance(qv, str):
            if qv == cv: puan += agirlik
        else:
            if cv != 0 and abs(qv - cv) / max(cv, 1) <= tolerans:
                puan += agirlik

    karsilastir(qf["kalinlik"], cf["kalinlik"], 25)
    karsilastir(qf["en"], cf["en"], 20)
    karsilastir(qf["boy"], cf["boy"], 20)
    karsilastir(qf["cap"], cf["cap"], 25)
    karsilastir(qf["profil_tipi"], cf["profil_tipi"], 20)
    karsilastir(qf["profil_olcu"], cf["profil_olcu"], 20)
    karsilastir(qf["kalite"], cf["kalite"], 20)
    karsilastir(qf["standart"], cf["standart"], 15)
    karsilastir(qf["kaplama"], cf["kaplama"], 10)

    if agirlik_toplam == 0:
        return None  # karşılaştırılabilir hiçbir alan yok
    return round(100 * puan / agirlik_toplam)


def normalize(s):
    if not s: return ""
    s = str(s)
    s = s.translate(_TR_BUYUK)
    s = s.lower()
    s = s.translate(_TR_KUCUK)
    s = re.sub(r'ø\s*', '', s)
    s = re.sub(r'[×*]', 'x', s)
    for _ in range(5):
        s = re.sub(r'(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)', r'\1 \2', s)
    s = re.sub(r'(\d)(mm|cm|kg|gr|gram|ml|lt|l|m|cm2|m2|m3)\b', r'\1 \2', s)
    # Noktalama + eşittir + tireler → boşluk ("L=2400" → "l 2400")
    s = re.sub(r'[\-_/,;:()\[\]{}+=]+', ' ', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def tokenize(s):
    raw = normalize(s).split()
    out = []
    for t in raw:
        if not t: continue
        if len(t) < 2 and not t.isdigit(): continue
        t = _KISALTMA.get(t, t)
        out.append(t)
    return out


def _benzerlik_set(qset, qsayilar, tset, tsayilar):
    if not qset or not tset: return 0
    ortak = qset & tset
    toplam = qset | tset
    if not toplam: return 0
    jaccard = len(ortak) / len(toplam)
    if qsayilar or tsayilar:
        s_ortak = qsayilar & tsayilar
        s_toplam = qsayilar | tsayilar
        sayi_jac = len(s_ortak) / len(s_toplam) if s_toplam else 0
    else:
        sayi_jac = 1.0
    puan = 0.55 * jaccard + 0.45 * sayi_jac
    if qset.issubset(tset):
        puan += 0.10
    return min(100, round(100 * puan))


def benzerlik(sorgu, hedef):
    """Eski API uyumluluğu — geriye dönük"""
    if not sorgu or not hedef: return 0
    qt = tokenize(sorgu)
    ht = tokenize(hedef)
    if not qt or not ht: return 0
    return _benzerlik_set(
        set(qt), {t for t in qt if any(c.isdigit() for c in t)},
        set(ht), {t for t in ht if any(c.isdigit() for c in t)}
    )


# ───────────────────────────────────────────────────────────────────────────
# IN-MEMORY CACHE — Pre-tokenize'lı katalog
# ───────────────────────────────────────────────────────────────────────────

_KATALOG_CACHE = None
_KATALOG_YUKLENME_SURE = 0
_KATALOG_BUCKETS = {}


def _katalog_cache_yukle():
    """Tüm katalogu RAM'e yükle. İlk sorguda çağrılır."""
    global _KATALOG_CACHE, _KATALOG_YUKLENME_SURE
    bas = time.time()
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT malzeme_no, kisa_metin_tr, uzun_metin_tr,
                   mal_grubu, degerleme_sinifi, olcu_birimi,
                   malzeme_turu, siparis_no
            FROM malzeme_katalog
        """).fetchall()
        cache = []
        buckets = {}
        for r in rows:
            kisa = r["kisa_metin_tr"] or ""
            uzun = r["uzun_metin_tr"] or ""
            kt = tokenize(kisa)
            ut = tokenize(uzun)
            ozf = ozellik_cikar((kisa + " " + uzun).strip())
            kayit = {
                "malzeme_no":        r["malzeme_no"],
                "kisa_metin":        kisa,
                "uzun_metin":        uzun,
                "mal_grubu":         r["mal_grubu"] or "",
                "degerleme_sinifi":  r["degerleme_sinifi"] or "",
                "olcu_birimi":       r["olcu_birimi"] or "",
                "malzeme_turu":      r["malzeme_turu"] or "",
                "siparis_no":        r["siparis_no"] or "",
                "kisa_set":          set(kt),
                "kisa_sayilar":      {t for t in kt if any(c.isdigit() for c in t)},
                "uzun_set":          set(ut),
                "uzun_sayilar":      {t for t in ut if any(c.isdigit() for c in t)},
                "ozellik":           ozf,
            }
            cache.append(kayit)
            buckets.setdefault(ozf["tip"], []).append(kayit)
        _KATALOG_CACHE = cache
        _KATALOG_BUCKETS = buckets
    finally:
        conn.close()
    _KATALOG_YUKLENME_SURE = round(time.time() - bas, 2)


def _katalog_cache_al():
    global _KATALOG_CACHE
    if _KATALOG_CACHE is None:
        _katalog_cache_yukle()
    return _KATALOG_CACHE


def _katalog_cache_temizle():
    global _KATALOG_CACHE, _KATALOG_BUCKETS
    _KATALOG_CACHE = None
    _KATALOG_BUCKETS = {}


# ───────────────────────────────────────────────────────────────────────────
# ARAMA
# ───────────────────────────────────────────────────────────────────────────

TAM_ESLESME_ESIK   = 90
YAKIN_ESLESME_ESIK = 40
OLASI_ESLESME_ESIK = 10
MAKSIMUM_YAKIN     = 20
MAKSIMUM_OLASI     = 8


def _ara(sorgu, min_puan=OLASI_ESLESME_ESIK, max_aday=200, ozellik_override=None):
    qt = tokenize(sorgu)
    if not qt: return []
    qset = set(qt)
    qsayilar = {t for t in qt if any(c.isdigit() for c in t)}
    qozellik = ozellik_override if ozellik_override else ozellik_cikar(sorgu)

    katalog = _katalog_cache_al()
    if not katalog: return []

    # Tip belirlenebiliyorsa aday havuzunu daralt (performans)
    if qozellik["tip"] != "diger" and _KATALOG_BUCKETS:
        adaylar = _KATALOG_BUCKETS.get(qozellik["tip"], []) + _KATALOG_BUCKETS.get("diger", [])
    else:
        adaylar = katalog

    sonuclar = []
    for c in adaylar:
        p_kisa = _benzerlik_set(qset, qsayilar, c["kisa_set"], c["kisa_sayilar"]) if c["kisa_set"] else 0
        p_uzun = _benzerlik_set(qset, qsayilar, c["uzun_set"], c["uzun_sayilar"]) if c["uzun_set"] else 0
        token_puan = max(p_kisa, p_uzun)

        oz_puan = _ozellik_puan(qozellik, c["ozellik"])
        if oz_puan is None:
            en_iyi = token_puan
        else:
            en_iyi = round(0.5 * token_puan + 0.5 * oz_puan)

        if en_iyi >= min_puan:
            sonuclar.append({
                "malzeme_no":       c["malzeme_no"],
                "kisa_metin":       c["kisa_metin"],
                "uzun_metin":       c["uzun_metin"],
                "mal_grubu":        c["mal_grubu"],
                "degerleme_sinifi": c["degerleme_sinifi"],
                "olcu_birimi":      c["olcu_birimi"],
                "malzeme_turu":     c["malzeme_turu"],
                "puan":             en_iyi,
                "puan_kisa":        p_kisa,
                "puan_uzun":        p_uzun,
                "puan_ozellik":     oz_puan,
                "ozellikler":       c["ozellik"],
            })

    sonuclar.sort(key=lambda x: x["puan"], reverse=True)
    return sonuclar[:max_aday]


def _kategori_belirle(sonuclar):
    if not sonuclar: return "yok"
    if sonuclar[0]["puan"] >= TAM_ESLESME_ESIK:   return "tam"
    if sonuclar[0]["puan"] >= YAKIN_ESLESME_ESIK: return "yakin"
    return "yok"


# ───────────────────────────────────────────────────────────────────────────
# KATALOG BİLGİSİ
# ───────────────────────────────────────────────────────────────────────────

@malzeme_bp.route("/api/malzeme/katalog-bilgi", methods=["GET"])
def malzeme_katalog_bilgi():
    conn = get_conn()
    try:
        toplam = conn.execute("SELECT COUNT(*) AS n FROM malzeme_katalog").fetchone()["n"]
        son = conn.execute("SELECT MAX(guncelleme_tarihi) AS s FROM malzeme_katalog").fetchone()["s"]
        log = conn.execute("SELECT * FROM malzeme_import_log ORDER BY id DESC LIMIT 5").fetchall()

        cache_durumu = "Yüklü değil"
        cache_n = 0
        if _KATALOG_CACHE is not None:
            cache_durumu = f"RAM'de aktif ({_KATALOG_YUKLENME_SURE}s ile yüklenmiş)"
            cache_n = len(_KATALOG_CACHE)

        return jsonify({
            "durum":           "ok",
            "toplam_kayit":    toplam,
            "son_guncelleme":  son,
            "son_import_log":  [dict(r) for r in log],
            "cache_durumu":    cache_durumu,
            "cache_kayit":     cache_n
        })
    except sqlite3.OperationalError as e:
        return jsonify({"durum": "hata", "mesaj": str(e), "toplam_kayit": 0}), 200
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# TEK MALZEME KONTROL
# ───────────────────────────────────────────────────────────────────────────

@malzeme_bp.route("/api/malzeme/kontrol", methods=["POST"])
def malzeme_kontrol():
    d = request.get_json(silent=True) or {}
    sorgu = (d.get("metin") or "").strip()
    ozellik_override = _override_normalize(d.get("ozellik_override"))

    if not sorgu:
        return jsonify({"durum": "hata", "mesaj": "Sorgu boş"}), 400
    if len(sorgu) < 3:
        return jsonify({"durum": "hata", "mesaj": "En az 3 karakter girin"}), 400

    bas = time.time()
    katalog = _katalog_cache_al()
    if not katalog:
        return jsonify({
            "durum":            "ok",
            "sorgu":            sorgu,
            "kategori":         "yok",
            "katalog_bos":      True,
            "mesaj":            "Katalog boş — önce MM60 dosyasını yükleyin",
            "tam_eslesmeler":   [],
            "yakin_eslesmeler": [],
            "olasi_eslesmeler": []
        })

    sonuclar = _ara(sorgu, min_puan=OLASI_ESLESME_ESIK, max_aday=200, ozellik_override=ozellik_override)
    kategori = _kategori_belirle(sonuclar)

    tam_list   = [s for s in sonuclar if s["puan"] >= TAM_ESLESME_ESIK]
    yakin_list = [s for s in sonuclar if YAKIN_ESLESME_ESIK <= s["puan"] < TAM_ESLESME_ESIK][:MAKSIMUM_YAKIN]
    olasi_list = [s for s in sonuclar if OLASI_ESLESME_ESIK <= s["puan"] < YAKIN_ESLESME_ESIK][:MAKSIMUM_OLASI]

    sure = round((time.time() - bas) * 1000)

    return jsonify({
        "durum":            "ok",
        "sorgu":            sorgu,
        "kategori":         kategori,
        "katalog_toplam":   len(katalog),
        "arama_sure_ms":    sure,
        "tam_eslesmeler":   tam_list,
        "yakin_eslesmeler": yakin_list,
        "olasi_eslesmeler": olasi_list,
        "tokens_test":      tokenize(sorgu),
        "ozellikler_test":  ozellik_override if ozellik_override else ozellik_cikar(sorgu),
        "ozellik_duzeltildi_mi": ozellik_override is not None
    })

# ───────────────────────────────────────────────────────────────────────────
# TOPLU KONTROL
# ───────────────────────────────────────────────────────────────────────────

@malzeme_bp.route("/api/malzeme/kontrol-toplu", methods=["POST"])
def malzeme_kontrol_toplu():
    d = request.get_json(silent=True) or {}
    sorgular = d.get("metinler") or []
    sorgular = [s.strip() for s in sorgular if s and s.strip()]

    if not sorgular:
        return jsonify({"durum": "hata", "mesaj": "Boş liste"}), 400
    if len(sorgular) > 500:
        return jsonify({"durum": "hata", "mesaj": "En fazla 500 satır"}), 400

    katalog = _katalog_cache_al()
    if not katalog:
        return jsonify({"durum": "hata", "mesaj": "Katalog boş"}), 200

    bas = time.time()
    toplu_sonuc = []
    for q in sorgular:
        if len(q) < 3:
            toplu_sonuc.append({
                "sorgu": q, "kategori": "hata",
                "mesaj": "Çok kısa", "en_iyi": None, "eslesme_sayisi": 0
            })
            continue
        sonuclar = _ara(q, min_puan=OLASI_ESLESME_ESIK, max_aday=20)
        kategori = _kategori_belirle(sonuclar)
        en_iyi = sonuclar[0] if sonuclar else None
        toplu_sonuc.append({
            "sorgu":          q,
            "kategori":       kategori,
            "en_iyi":         en_iyi,
            "ilk_3":          sonuclar[:3],
            "eslesme_sayisi": len([s for s in sonuclar if s["puan"] >= YAKIN_ESLESME_ESIK])
        })

    sure = round((time.time() - bas) * 1000)
    ozet = {
        "toplam": len(toplu_sonuc),
        "tam":    sum(1 for r in toplu_sonuc if r["kategori"] == "tam"),
        "yakin":  sum(1 for r in toplu_sonuc if r["kategori"] == "yakin"),
        "yok":    sum(1 for r in toplu_sonuc if r["kategori"] == "yok"),
        "hata":   sum(1 for r in toplu_sonuc if r["kategori"] == "hata")
    }

    return jsonify({
        "durum":          "ok",
        "ozet":           ozet,
        "sonuclar":       toplu_sonuc,
        "katalog_toplam": len(katalog),
        "arama_sure_ms":  sure
    })


# ───────────────────────────────────────────────────────────────────────────
# TAM BOY ARAMA — Fire/parça malzemesinden tam boy versiyonlarını bul
# ───────────────────────────────────────────────────────────────────────────

_FIRE_KELIMELERI = [
    "fire", "fıre", "firè",
    "parca", "parça", "parcasi", "parçası",
    "kirpinti", "kırpıntı", "kalintı", "kalıntı",
    "artik", "artık", "atik", "atık",
    "kesilmis", "kesilmiş", "kesik",
    "ucu", "kalan"
]

_DEFAULT_TAM_BOYLAR = [6000, 12000]


def _sorgu_temizle_fire(sorgu):
    """Fire kelimelerini ve L=XXX pattern'larını çıkar"""
    s = sorgu
    for kelime in _FIRE_KELIMELERI:
        s = re.sub(r'\b' + re.escape(kelime) + r'\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bl\s*[=:]\s*\d+\s*(?:mm)?\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\buzunluk\s*[=:]\s*\d+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\bboy\s*[=:]\s*\d+', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


@malzeme_bp.route("/api/malzeme/tam-boylar", methods=["POST"])
def malzeme_tam_boylar():
    """
    Bir fire/parça malzemesinin tam boy versiyonlarını bul.

    Body: {
      metin: "IPE FİRE 300 (30Б2) L=2400 C255",
      tam_boylar: [6000, 12000]   // opsiyonel
    }
    """
    d = request.get_json(silent=True) or {}
    sorgu = (d.get("metin") or "").strip()
    tam_boylar = d.get("tam_boylar") or _DEFAULT_TAM_BOYLAR

    if not sorgu:
        return jsonify({"durum": "hata", "mesaj": "Sorgu boş"}), 400
    if len(sorgu) < 3:
        return jsonify({"durum": "hata", "mesaj": "En az 3 karakter girin"}), 400

    bas = time.time()
    katalog = _katalog_cache_al()
    if not katalog:
        return jsonify({
            "durum": "hata",
            "mesaj": "Katalog boş — önce MM60 yükleyin"
        }), 400

    sorgu_temiz = _sorgu_temizle_fire(sorgu)

    if not sorgu_temiz:
        return jsonify({
            "durum": "hata",
            "mesaj": "Sorgu temizlendikten sonra anlamlı içerik kalmadı"
        }), 400

    gruplar = {}
    toplam_bulunan = 0

    for boy in tam_boylar:
        yeni_sorgu = f"{sorgu_temiz} {boy}"
        sonuclar = _ara(yeni_sorgu, min_puan=20, max_aday=50)

        boy_str = str(boy)
        filtreli = []
        for s in sonuclar:
            uzun_n = normalize(s["uzun_metin"] or "")
            kisa_n = normalize(s["kisa_metin"] or "")
            hedef = uzun_n + " " + kisa_n
            hedef_tokens = hedef.split()

            if boy_str in hedef_tokens:
                # Fire kelimesi olan kayıtları atla
                fire_var = any(fk in hedef_tokens for fk in _FIRE_KELIMELERI)
                if not fire_var:
                    s_copy = dict(s)
                    s_copy["tam_boy"] = boy
                    filtreli.append(s_copy)

        filtreli.sort(key=lambda x: x["puan"], reverse=True)
        gruplar[str(boy)] = filtreli[:15]
        toplam_bulunan += len(filtreli)

    sure = round((time.time() - bas) * 1000)

    return jsonify({
        "durum":           "ok",
        "sorgu_orijinal":  sorgu,
        "sorgu_temiz":     sorgu_temiz,
        "tam_boylar":      tam_boylar,
        "gruplar":         gruplar,
        "toplam_bulunan":  toplam_bulunan,
        "arama_sure_ms":   sure,
        "katalog_toplam":  len(katalog)
    })


# ───────────────────────────────────────────────────────────────────────────
# DEBUG (Veri sorunu teşhisi)
# ───────────────────────────────────────────────────────────────────────────

@malzeme_bp.route("/api/malzeme/debug", methods=["GET", "POST"])
def malzeme_debug():
    """
    Veri yapısı + token analizi + en yakın 30 sonuç (eşiksiz).
    GET: /api/malzeme/debug?sorgu=civata m16x60
    """
    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        sorgu = (d.get("sorgu") or "").strip()
    else:
        sorgu = (request.args.get("sorgu") or "").strip()

    conn = get_conn()
    try:
        toplam = conn.execute("SELECT COUNT(*) AS n FROM malzeme_katalog").fetchone()["n"]
        bos_kisa = conn.execute(
            "SELECT COUNT(*) AS n FROM malzeme_katalog WHERE kisa_metin_tr IS NULL OR kisa_metin_tr = ''"
        ).fetchone()["n"]
        bos_uzun = conn.execute(
            "SELECT COUNT(*) AS n FROM malzeme_katalog WHERE uzun_metin_tr IS NULL OR uzun_metin_tr = ''"
        ).fetchone()["n"]

        ornek = conn.execute("""
            SELECT malzeme_no, kisa_metin_tr, uzun_metin_tr, mal_grubu,
                   degerleme_sinifi, olcu_birimi
            FROM malzeme_katalog
            WHERE kisa_metin_tr != '' OR uzun_metin_tr != ''
            LIMIT 5
        """).fetchall()

        token_arama = []
        if sorgu:
            qt = tokenize(sorgu)
            for t in qt[:5]:
                if len(t) >= 3:
                    rows = conn.execute("""
                        SELECT malzeme_no, kisa_metin_tr, uzun_metin_tr
                        FROM malzeme_katalog
                        WHERE LOWER(kisa_metin_tr) LIKE ? OR LOWER(uzun_metin_tr) LIKE ?
                        LIMIT 5
                    """, (f"%{t}%", f"%{t}%")).fetchall()
                    token_arama.append({
                        "token":    t,
                        "ham_sql_eslesme_sayisi": conn.execute(
                            "SELECT COUNT(*) AS n FROM malzeme_katalog WHERE LOWER(kisa_metin_tr) LIKE ? OR LOWER(uzun_metin_tr) LIKE ?",
                            (f"%{t}%", f"%{t}%")
                        ).fetchone()["n"],
                        "ornekler": [dict(r) for r in rows]
                    })

        sonuc = {
            "durum":              "ok",
            "katalog_toplam":     toplam,
            "bos_kisa_metin":     bos_kisa,
            "bos_uzun_metin":     bos_uzun,
            "kisa_bos_yuzde":     round(100 * bos_kisa / toplam, 1) if toplam else 0,
            "uzun_bos_yuzde":     round(100 * bos_uzun / toplam, 1) if toplam else 0,
            "ornek_kayit_5":      [dict(r) for r in ornek],
            "cache_yuklu":        _KATALOG_CACHE is not None,
            "cache_kayit_sayisi": len(_KATALOG_CACHE) if _KATALOG_CACHE else 0,
            "cache_yuklenme_s":   _KATALOG_YUKLENME_SURE
        }

        if sorgu:
            sonuc["sorgu"]            = sorgu
            sonuc["sorgu_normalize"]  = normalize(sorgu)
            sonuc["sorgu_tokens"]     = tokenize(sorgu)
            sonuc["token_arama"]      = token_arama

            tum_sonuclar = _ara(sorgu, min_puan=0, max_aday=30)
            sonuc["en_yakin_30"]      = tum_sonuclar[:30]
            sonuc["en_yakin_sayisi"]  = len(tum_sonuclar)

        return jsonify(sonuc)
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# TOKENS YENİDEN HESAPLA
# ───────────────────────────────────────────────────────────────────────────

@malzeme_bp.route("/api/malzeme/tokens-yeniden-hesapla", methods=["POST"])
def malzeme_tokens_yeniden_hesapla():
    """Tokens_tr kolonunu tüm katalog için yeniden hesapla."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT malzeme_no, kisa_metin_tr, uzun_metin_tr FROM malzeme_katalog").fetchall()
        guncellenenler = []
        for r in rows:
            kisa = r["kisa_metin_tr"] or ""
            uzun = r["uzun_metin_tr"] or ""
            tokens = " ".join(tokenize(kisa + " " + uzun))
            guncellenenler.append((tokens, r["malzeme_no"]))
        conn.executemany(
            "UPDATE malzeme_katalog SET tokens_tr = ? WHERE malzeme_no = ?",
            guncellenenler
        )
        conn.commit()
        _katalog_cache_temizle()
        return jsonify({
            "durum": "ok",
            "mesaj": f"{len(guncellenenler)} kayıt için tokens yeniden hesaplandı",
            "kayit_sayisi": len(guncellenenler)
        })
    except Exception as e:
        try: conn.rollback()
        except: pass
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────────────────────
# KATALOG EXCEL IMPORT — Akıllı sütun eşleştirme
# ───────────────────────────────────────────────────────────────────────────

def _normalize_kolon(s):
    """Excel kolon adını normalize et — TR karakterler, küçük harf, özel karakter temizliği"""
    s = str(s).lower().strip()
    # Türkçe karakterler
    s = s.translate(str.maketrans({
        'ı':'i','ş':'s','ğ':'g','ü':'u','ö':'o','ç':'c',
        'İ':'i','I':'i','Ş':'s','Ğ':'g','Ü':'u','Ö':'o','Ç':'c'
    }))
    # Noktalama/özel karakterleri boşluğa
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    # Çoklu boşluk → tek
    s = re.sub(r'\s+', ' ', s).strip()
    return s


# Normalize edilmiş alias'lar — gerçek SAP MM60 export sütun adları
_SUTUN_ALIAS_RAW = {
    "malzeme_no": [
        "malzeme", "malz no", "matnr", "material", "malzeme no",
        "kod", "sap kodu", "sap kod", "malzeme kodu", "malzeme numarasi"
    ],
    "kisa_metin_tr": [
        "kisa metin", "kisa metin tr", "kisa aciklama", "kisa tanim",
        "malzeme kisa metni", "malzeme kisa metini", "malzeme kisa tanim",
        "malzeme kisa tanimi", "malzeme kisa aciklama", "malzeme kisa aciklamasi",
        "kisa metin malzeme", "malzeme aciklama", "tanim", "malzeme tanim",
        "malzeme tanimi"
    ],
    "kisa_metin_en": [
        "kisa metin en", "short text en", "short text", "kisa metin ing", "english short text"
    ],
    "kisa_metin_ru": [
        "kisa metin ru", "short text ru", "kisa metin rusca", "russian short text"
    ],
    "uzun_metin_tr": [
        "uzun metin", "uzun metin tr", "uzun aciklama", "uzun tanim",
        "malzeme uzun tanimi", "malzeme uzun metni", "malzeme uzun aciklama",
        "malzeme uzun aciklamasi", "uzun metin malzeme", "uzun aciklama tr",
        "malzeme uzun metin", "detay tanim"
    ],
    "uzun_metin_en": [
        "uzun metin en", "long text en", "long text", "uzun metin ing", "english long text"
    ],
    "uzun_metin_ru": [
        "uzun metin ru", "long text ru", "uzun metin rusca", "russian long text"
    ],
    "mal_grubu": [
        "mal grubu", "matkl", "grup", "malzeme grubu", "mal grup"
    ],
    "degerleme_sinifi": [
        "degerleme sinifi", "deg sin", "deg sn", "valuation class",
        "degerleme", "degerlendirme sinifi", "deger sinifi"
    ],
    "olcu_birimi": [
        "olcu birimi", "birim", "uom", "temel olcu birimi", "olcu",
        "ana olcu birimi", "base unit", "stok birimi", "ana birim"
    ],
    "malzeme_turu": [
        "malzeme turu", "mtart", "material type", "malzeme tipi", "tur"
    ],
    "siparis_no": [
        "siparis no", "siparis numarasi", "siparis", "order no"
    ]
}

# Normalize edilmiş halini önceden hesapla
_SUTUN_ALIAS = {
    std: [_normalize_kolon(a) for a in aliases]
    for std, aliases in _SUTUN_ALIAS_RAW.items()
}


def _sutun_eslestir(df_cols):
    """
    Excel kolonlarını standart isimlere eşle.
    1) Önce exact match (normalize edilmiş)
    2) Bulamazsa substring fallback (akıllı tahmin)
    """
    mapping = {}
    df_cols_norm = [(c, _normalize_kolon(c)) for c in df_cols]

    # 1. Exact alias match (normalize edilmiş)
    for std, aliases in _SUTUN_ALIAS.items():
        for col_orig, col_norm in df_cols_norm:
            if col_norm in aliases:
                if std not in mapping:
                    mapping[std] = col_orig
                    break

    # 2. Substring fallback — kalan alanlar için akıllı tahmin
    for col_orig, col_norm in df_cols_norm:
        if not col_norm: continue

        # Bu sütun zaten başka bir alana eşlendiyse atla
        if col_orig in mapping.values():
            continue

        # Malzeme no
        if "malzeme_no" not in mapping:
            if (col_norm == "malzeme" or
                ("malzeme" in col_norm and ("no" in col_norm.split() or "kod" in col_norm.split())) or
                col_norm == "matnr" or col_norm == "material"):
                if "grub" not in col_norm and "tur" not in col_norm and "metin" not in col_norm and "tanim" not in col_norm:
                    mapping["malzeme_no"] = col_orig
                    continue

        # Kısa metin TR
        if "kisa_metin_tr" not in mapping:
            if "kisa" in col_norm and ("metin" in col_norm or "tanim" in col_norm or "aciklama" in col_norm):
                if " en" not in " " + col_norm and " ru" not in " " + col_norm and "ing" not in col_norm and "rusca" not in col_norm:
                    mapping["kisa_metin_tr"] = col_orig
                    continue

        # Uzun metin TR
        if "uzun_metin_tr" not in mapping:
            if "uzun" in col_norm and ("metin" in col_norm or "tanim" in col_norm or "aciklama" in col_norm):
                if " en" not in " " + col_norm and " ru" not in " " + col_norm and "ing" not in col_norm and "rusca" not in col_norm:
                    mapping["uzun_metin_tr"] = col_orig
                    continue

        # Mal grubu
        if "mal_grubu" not in mapping:
            if ("mal" in col_norm and "grub" in col_norm) or col_norm == "matkl":
                mapping["mal_grubu"] = col_orig
                continue

        # Değerleme sınıfı
        if "degerleme_sinifi" not in mapping:
            if "degerleme" in col_norm or "deger" in col_norm.split():
                mapping["degerleme_sinifi"] = col_orig
                continue

        # Ölçü birimi
        if "olcu_birimi" not in mapping:
            if "olcu" in col_norm or col_norm == "uom" or "birim" in col_norm:
                mapping["olcu_birimi"] = col_orig
                continue

        # Malzeme türü
        if "malzeme_turu" not in mapping:
            if ("malzeme" in col_norm and ("tur" in col_norm.split() or "tip" in col_norm.split())) or col_norm == "mtart":
                mapping["malzeme_turu"] = col_orig
                continue

        # Sipariş no
        if "siparis_no" not in mapping:
            if "siparis" in col_norm:
                mapping["siparis_no"] = col_orig
                continue

    return mapping


@malzeme_bp.route("/api/malzeme/katalog-import", methods=["POST"])
def malzeme_katalog_import():
    if pd is None:
        return jsonify({"durum": "hata", "mesaj": "pandas kurulu değil"}), 500
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya gönderilmedi"}), 400

    f = request.files["dosya"]
    dosya_adi = f.filename or "mm60.xlsx"

    try:
        df = pd.read_excel(f, dtype=str)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": f"Excel okunamadı: {e}"}), 400

    df = df.fillna("")
    mapping = _sutun_eslestir(df.columns)

    if "malzeme_no" not in mapping:
        return jsonify({
            "durum": "hata",
            "mesaj": "Excel'de 'Malzeme' kolonu bulunamadı.",
            "bulunan_kolonlar": list(df.columns),
            "eslenen_kolonlar": mapping
        }), 400

    # Eşlenmeyen Excel kolonlarını tespit et (kullanıcıya rapor için)
    eslenen_excel_kolonlari = set(mapping.values())
    eslenmeyen_kolonlar = [c for c in df.columns if c not in eslenen_excel_kolonlari]
    bos_alan_uyarisi = []
    for kritik in ["kisa_metin_tr", "uzun_metin_tr", "mal_grubu", "olcu_birimi", "degerleme_sinifi"]:
        if kritik not in mapping:
            bos_alan_uyarisi.append(kritik)

    kayitlar = []
    for _, satir in df.iterrows():
        malz_no = str(satir.get(mapping["malzeme_no"], "")).strip()
        if not malz_no or malz_no.lower() == "nan":
            continue
        kisa_tr = str(satir.get(mapping.get("kisa_metin_tr", ""), "")).strip()
        uzun_tr = str(satir.get(mapping.get("uzun_metin_tr", ""), "")).strip()
        tokens_str = " ".join(tokenize(kisa_tr + " " + uzun_tr))
        kayitlar.append((
            malz_no, kisa_tr,
            str(satir.get(mapping.get("kisa_metin_en", ""), "")).strip(),
            str(satir.get(mapping.get("kisa_metin_ru", ""), "")).strip(),
            uzun_tr,
            str(satir.get(mapping.get("uzun_metin_en", ""), "")).strip(),
            str(satir.get(mapping.get("uzun_metin_ru", ""), "")).strip(),
            str(satir.get(mapping.get("mal_grubu", ""), "")).strip(),
            str(satir.get(mapping.get("degerleme_sinifi", ""), "")).strip(),
            str(satir.get(mapping.get("olcu_birimi", ""), "")).strip(),
            str(satir.get(mapping.get("malzeme_turu", ""), "")).strip(),
            str(satir.get(mapping.get("siparis_no", ""), "")).strip(),
            tokens_str
        ))

    if not kayitlar:
        return jsonify({"durum": "hata", "mesaj": "Geçerli kayıt yok"}), 400

    conn = get_conn()
    try:
        conn.executemany("""
            INSERT INTO malzeme_katalog
                (malzeme_no, kisa_metin_tr, kisa_metin_en, kisa_metin_ru,
                 uzun_metin_tr, uzun_metin_en, uzun_metin_ru,
                 mal_grubu, degerleme_sinifi, olcu_birimi, malzeme_turu,
                 siparis_no, tokens_tr)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(malzeme_no) DO UPDATE SET
                kisa_metin_tr     = excluded.kisa_metin_tr,
                kisa_metin_en     = excluded.kisa_metin_en,
                kisa_metin_ru     = excluded.kisa_metin_ru,
                uzun_metin_tr     = excluded.uzun_metin_tr,
                uzun_metin_en     = excluded.uzun_metin_en,
                uzun_metin_ru     = excluded.uzun_metin_ru,
                mal_grubu         = excluded.mal_grubu,
                degerleme_sinifi  = excluded.degerleme_sinifi,
                olcu_birimi       = excluded.olcu_birimi,
                malzeme_turu      = excluded.malzeme_turu,
                siparis_no        = excluded.siparis_no,
                tokens_tr         = excluded.tokens_tr,
                guncelleme_tarihi = CURRENT_TIMESTAMP
        """, kayitlar)
        conn.execute(
            "INSERT INTO malzeme_import_log (dosya_adi, satir_sayisi, pc_adi) VALUES (?, ?, ?)",
            (dosya_adi, len(kayitlar), socket.gethostname())
        )
        conn.commit()
        _katalog_cache_temizle()
        toplam = conn.execute("SELECT COUNT(*) AS n FROM malzeme_katalog").fetchone()["n"]
        return jsonify({
            "durum": "ok",
            "mesaj": f"{len(kayitlar)} kayıt aktarıldı/güncellendi",
            "ictekayit_sayisi": len(kayitlar),
            "katalog_toplam": toplam,
            "eslenen_kolonlar": mapping,
            "eslenmeyen_excel_kolonlari": eslenmeyen_kolonlar,
            "eksik_alan_uyarisi": bos_alan_uyarisi,
            "bulunan_excel_kolonlari": list(df.columns)
        })
    except Exception as e:
        try: conn.rollback()
        except: pass
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@malzeme_bp.route("/api/malzeme/katalog-temizle", methods=["POST"])
def malzeme_katalog_temizle():
    d = request.get_json(silent=True) or {}
    if d.get("onay", "") != "SIL":
        return jsonify({"durum": "hata", "mesaj": "Onay metni 'SIL' olmalı"}), 400
    conn = get_conn()
    try:
        cnt = conn.execute("SELECT COUNT(*) AS n FROM malzeme_katalog").fetchone()["n"]
        conn.execute("DELETE FROM malzeme_katalog")
        conn.commit()
        _katalog_cache_temizle()
        return jsonify({"durum": "ok", "mesaj": f"{cnt} kayıt silindi"})
    finally:
        conn.close()