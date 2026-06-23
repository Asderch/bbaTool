"""
MB52 STOK MODÜLÜ — Ambar Data v5
- Mal grubu artık MB52'den (MZ-XXXX kodları)
- Eşleştirme: MAL_GRUBU_ADLARI sabit listesi
- Tahmin fonksiyonu fallback olarak kaldı (MB52'de mal grubu yoksa)
"""
import os
import sys
import sqlite3
import re
from datetime import datetime
from flask import Blueprint, request, jsonify

try:
    import pandas as pd
except ImportError:
    pd = None

mb52_bp = Blueprint("mb52", __name__)

# ═══════════════════════════════════════════════════════════════════════════
# MAL GRUBU SÖZLÜĞÜ — SAP MZ-XXXX kodları
# ═══════════════════════════════════════════════════════════════════════════

_MAL_GRUBU_DATA = """\
MZ-2001	NERVÜRLÜ DEMİRLER
MZ-2002	KÖŞEBENT
MZ-2003	I-PROFİL
MZ-2004	U-PROFİL
MZ-2005	KUTU PROFİL
MZ-2006	SAC PASLANMAZ
MZ-2007	LAMALAR
MZ-2008	SAC
MZ-2009	SAC
MZ-2010	SAC
MZ-2011	SAC GALVANİZ
MZ-2012	SAC TRAPEZ
MZ-2013	ATERMİT-KREMİT-ÇATI
MZ-2014	TUĞLA
MZ-2015	BİMSLER
MZ-2016	ALÇI ÇEŞİTLERİ
MZ-2017	DERZ + YAPIŞTIRICILA
MZ-2018	SERAMİKLER+FAYANSLAR
MZ-2019	MERMERLER
MZ-2020	GRANİTLER
MZ-2021	ÇİMENTOLAR
MZ-2022	HAZIR BETONLAR
MZ-2023	CAMLAR
MZ-2024	HASIR ÇELİKLER
MZ-2025	BETON KATKILARI
MZ-2026	KUM-ÇAKIL
MZ-2027	STRAFORLAR
MZ-2028	YTONGLAR
MZ-2029	KORUGE BORULAR
MZ-2030	BORU
MZ-2031	FİTTİNGS
MZ-2032	BOYALAR VE KATKILARI
MZ-2033	ISI YALITIMLAR
MZ-2034	Rögar kapağı
MZ-2035	BÜZ
MZ-2036	KALDIRIM-YOL TAŞ
MZ-2037	PARKELER
MZ-2038	KÜVETLER
MZ-2039	KLOZETLER
MZ-2040	PASPAYLAR
MZ-2041	CTP MALZEMELER
MZ-2042	HDPE MALZEMELER
MZ-2043	TRİPLERX MALZEMELER
MZ-2044	KERESTELER
MZ-2045	SU PROJE KULLANMA
MZ-2046	GENLEŞME DERZLERİ
MZ-2047	MESNETLER
MZ-2048	YOL BARİYERLERİ
MZ-2051	ZİFT
MZ-2052	ASFALT
MZ-2053	EMÜLSYON
MZ-2054	PATLAYICI MALZEMELER
MZ-2055	BİTÜM ÇEŞİTLERİ
MZ-2056	PREFABRİK YAPI MALZE
MZ-2057	MEMBRANLAR
MZ-2058	GEOTEKSTİL
MZ-2059	ISITMA VE HAVALANDIR
MZ-2060	YANGIN İHBAR MALZEML
MZ-2061	BİNA GÜVENLİK SİSTEM
MZ-2062	SU TUTUCU BANTLAR
MZ-2063	KALIP VE İSKELE YARD
MZ-2064	YAPI KİMYASAL MALZEM
MZ-2065	GÖMÜLÜ ELEMANLAR
MZ-2066	DEMİR BAĞLAMA MANŞON
MZ-2067	ALÇIPAN VE BETOPANLA
MZ-2068	KİMYASAL MALZEMELER
MZ-2069	AHŞAP KAPI VEPENCERE
MZ-2070	METAL ASMA TAVANLAR
MZ-2071	BİNA CEPHE KAPLAMALR
MZ-2072	MEKANİK GRUP OTOMASY
MZ-2073	MEKANİK RAFİNERİ BAĞ
MZ-2074	ÇEVRE DÜZENLEME MALZ
MZ-2075	ÇELİK ASTARLI BORULA
MZ-2076	DEMİR DÜZ
MZ-2077	IZGARA
MZ-2078	İŞLENMİŞ SACLAR
MZ-2999	GENEL İNŞAAT MALZEME
MZ-3001	YAĞ FİLİTRELERİ
MZ-3002	YAKIT FİLİTRELERİ
MZ-3003	HAVA FİLİTRELERİ
MZ-3004	ŞANZIMAN FİLİTRELERİ
MZ-3005	HİDROLİK FİLİTRELERİ
MZ-3006	SU FİLİTRELERİ
MZ-3007	KABİN FİLİTRELERİ
MZ-3008	POLEN FİLİTRELERİ
MZ-3009	O-RİNGLER
MZ-3010	KEÇELER
MZ-3011	RULMANLAR
MZ-3012	SEKMANLAR
MZ-3013	AKÜLER
MZ-3014	İÇ LASTİKLER
MZ-3015	DIŞ LASTİKLER
MZ-3016	KOLONLAR
MZ-3017	JANTLAR
MZ-3018	KAYIŞALAR
MZ-3019	OTO ELEKTİK MALZEMEL
MZ-3020	HİDROLİK HORTUMLAR
MZ-3021	HİDROLİK HORTUM BAŞL
MZ-3022	LİEBHERR YEDEKLERİ
MZ-3023	CAT YEDEKLERİ
MZ-3024	HUNDAİ YEDEKLERİ
MZ-3025	KOMATSU YEDEKLERİ
MZ-3026	CASE YEDEKLERİ
MZ-3027	BOMAG YEDEKLERİ
MZ-3028	HİTACHİ YEDEKLERİ
MZ-3029	RENAULT YEDEKLERİ
MZ-3030	BMW YEDEKLERİ
MZ-3031	FORD YEDEKLERİ
MZ-3032	BMC YEDEKLERİ
MZ-3033	IVECO YEDEKLERİ
MZ-3034	P&H YEDEKLERİ
MZ-3035	SUZİKİ YEDEKLERİ
MZ-3036	HİDROMEK YEDEKLERİ
MZ-3037	ATLAS-COPCO YEDEKLER
MZ-3038	AKSA YEDEKLERİ
MZ-3039	GENPOVER YEDEKLERİ
MZ-3040	FİAT YEDEKLERİ
MZ-3041	MERCEDES YEDEKLERİ
MZ-3042	NİSSAN YEDEKLERİ
MZ-3043	KRUPP YEDEKLERİ
MZ-3044	PERKİNS YEDEKLERİ
MZ-3045	TOFAŞ YEDEKLERİ
MZ-3046	HONDA YEDEKLERİ
MZ-3047	TOYOTA YEDEKLERİ
MZ-3048	VOLVO YEDEKLERİ
MZ-3049	VİBROMAX YEDEKLERİ
MZ-3051	WACKER YEDEKLERİ
MZ-3052	ÇUKUROVA YEDEKLERİ
MZ-3053	PUTZMAİSTER YEDEKLER
MZ-3054	FURUKAVA YEDEKLERİ
MZ-3055	GAMAK YEDEKLERİ
MZ-3056	GÖKER YEDEKLERİ
MZ-3057	NACE YEDEKLERİ
MZ-3058	HAMM YEDEKLERİ
MZ-3059	INNGERSOLLRAND YEDEK
MZ-3060	MASSENZA YEDEKLERİ
MZ-3061	FERMEC YEDEKLERİ
MZ-3062	ŞERBET VE SHOTCERETE
MZ-3063	PANCAR YEDEKLERİ
MZ-3064	SARMAK YEDEKLERİ
MZ-3065	TAMÇEKİ YEDEKLERİ
MZ-3066	TEZSAN YADEKLERİ
MZ-3067	VÖGELE YEDEKLERİ
MZ-3068	WEBER YEDEKLERİ
MZ-3069	ZİNCİRLER
MZ-3070	ZİNCİR DİŞLİLER
MZ-3071	KASNAKLAR
MZ-3072	KULE VİNÇ YEDEKLERİ
MZ-3073	BENZİN ÇEŞİTLERİ
MZ-3074	FUEL-OİL
MZ-3075	LPG VE LNG
MZ-3076	MOTORİN
MZ-3077	MADENİ YAĞLAR VE GRE
MZ-3078	KIRICI VE DELİCİ UÇL
MZ-3079	KAROT UÇLARI
MZ-3080	ELEKTRİK MOTORLARI
MZ-3081	MAN YEDEKLERİ
MZ-3082	ISTAVROZ
MZ-3083	MİTSUBİSHİ YEDEKLERİ
MZ-3084	SCHWING YEDEKLERİ
MZ-3085	ISIZU YEDEKLERİ
MZ-3086	SANDVİK YEDEKLERİ
MZ-3087	JLG YEDEKLERİ
MZ-3088	DAEWOO YEDEKLERİ
MZ-3089	ANCON YEDEKLERİ
MZ-3090	DEXTRA YEDEKLERİ
MZ-3999	GENEL YEDEK PARÇALAR
MZ-4001	KABLOLAR
MZ-4002	BIÇAKLI SİGORTALAR
MZ-4003	W OTOMATLAR
MZ-4004	KAÇAK AKIM RÖLELERİ
MZ-4005	PRİZLER
MZ-4006	FİŞLER
MZ-4007	AMPÜLLER
MZ-4008	GRUP PRİZLER
MZ-4009	KONTAKTÖRLER
MZ-4010	TERMİKLER MANYATİK Ş
MZ-4011	ŞARTELLER
MZ-4012	START-STOP BUTONLARI
MZ-4013	İZOLATÖRLER
MZ-4014	BAKIR İLETKEN MALZEM
MZ-4015	KABLO KANALLARI
MZ-4016	BUATLAR
MZ-4017	ANAHTAR-KOMİTATÖR
MZ-4018	EK MUFLAR
MZ-4019	PABUÇLAR
MZ-4020	ARMATÜRLER
MZ-4021	MOTOR KORUMA RÖLELER
MZ-4022	PROJEKTÖRLER
MZ-4023	KLAMENSLER
MZ-4024	PANOLAR
MZ-4025	REAKTİF GÜÇ RÖLELERİ
MZ-4026	AYDINLATMA DİREKLERİ
MZ-4027	ELEKTRİK DİREK/EKİPM
MZ-4028	JENERATÖRLER
MZ-4999	GENEL ELEKTİRİK MALZ
MZ-5001	TÜM PVC MALZEMELER
MZ-5002	TÜM PPRC MALZEMELER
MZ-5003	PUŞVİT MALZEMELER
MZ-5004	GALVANİZLİLER
MZ-5005	SİYAHLAR
MZ-5006	VANA ÇEŞİTLERİ
MZ-5007	HORTUMLAR
MZ-5008	BANYO AKSESUARLARI
MZ-5009	SARI MALZEMELER
MZ-5010	YATAY VE DİKEY POMPA
MZ-5011	ELEKTRO MEKANİK
MZ-5999	GENEL SIHHİ TESİSAT
MZ-6001	CİVATA ÇEŞİTLERİ
MZ-6002	SOMUN ÇEŞİTLERİ
MZ-6003	PULLAR
MZ-6004	VİDA ÇEŞİTLERİ
MZ-6005	SİLİKON-KÖPÜK-BALLY
MZ-6006	MAPALAR
MZ-6007	KALMENSLER
MZ-6008	DÜBELLER
MZ-6009	ÇİVİLER-TELLER
MZ-6010	MATKAP UCLARI
MZ-6011	MATKAP UCLARI ELMAS
MZ-6012	KAYNAK  SARF MALZ.
MZ-6013	KESME TAŞLARI
MZ-6014	TEKERLER
MZ-6015	KELEPÇELER
MZ-6016	BOTANİKA PARÇALAR
MZ-6017	GAZ ARMATÜRLERİ
MZ-6018	SAPANLAR
MZ-6019	BRANDA VE NAYLON ÖRT
MZ-6020	HALATLAR
MZ-6021	MATKAP UCLARI KIRICI
MZ-6999	GENEL HIRDAVAT MALZ.
MZ-7001	KİŞİSEL KORUYUCU EKİ
MZ-7002	İŞ GÜVENLİK LEVHALAR
MZ-7003	TRAFİK  LEVHALARI
MZ-7004	SAĞLIK MALZEMELERİ
MZ-8001	MATBU MALZEMELERİ
MZ-8002	KIRTASİYE MAZLEMELER
MZ-8003	ÇAY OCAĞI MALZEMELER
MZ-8004	TEMİZLİK MALZEMELERİ
MZ-8005	YİYECEK İÇECEKLER
MZ-8006	KAMP YARDIMCI MALZEM
MZ-8007	KIŞ BAKIM MALZEMELER
MZ-8008	MUTFAK MALZEMELERİ
MZ-8009	BORU ÜRETİM
"""

MAL_GRUBU_ADLARI = {}
for _line in _MAL_GRUBU_DATA.strip().split("\n"):
    _parts = _line.split("\t", 1)
    if len(_parts) == 2:
        MAL_GRUBU_ADLARI[_parts[0].strip()] = _parts[1].strip()


def mal_grubu_tanimi(kod):
    """MZ-2006 → 'SAC' veya bilinmiyorsa None"""
    if not kod:
        return None
    return MAL_GRUBU_ADLARI.get(str(kod).strip().upper())


# ═══════════════════════════════════════════════════════════════════════════
# KONFIGÜRASYON
# ═══════════════════════════════════════════════════════════════════════════

DB_DOSYA = "agirlik_hesaplama.db"
TABLO_ADAYLAR = ["hesaplama_satirlari", "agirlik_kayitlari_satirlar", "agirlik_satirlar"]


_db_path_cache = None

def db_path():
    """
    agirlik_hesaplama.db konumunu akıllıca tespit et.
    Birden fazla DB varsa: hesaplama_satirlari tablosu olan DB tercih edilir.
    Böylece agirlik_db.py ile aynı DB'ye yazar.
    """
    global _db_path_cache
    if _db_path_cache is not None and os.path.exists(_db_path_cache):
        return _db_path_cache

    # Olası tüm konumları topla
    candidates = []

    # 1) Bu .py dosyasının olduğu klasör (geliştirme = bba-tool, --onedir exe = _internal)
    file_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(file_dir)

    if getattr(sys, "frozen", False):
        # .exe paketinde — exe yanı da aday
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir not in candidates:
            candidates.append(exe_dir)

    # 2) Parent klasörler de aday (dist'in üstü, _internal'ın üstü gibi)
    for c in list(candidates):
        parent = os.path.dirname(c)
        if parent and parent != c and parent not in candidates:
            candidates.append(parent)

    # 3) Önce: hesaplama_satirlari tablosu olan bir DB var mı?
    for loc in candidates:
        p = os.path.join(loc, DB_DOSYA)
        if os.path.exists(p):
            try:
                conn = sqlite3.connect(p)
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='hesaplama_satirlari'"
                ).fetchone()
                conn.close()
                if row:
                    _db_path_cache = p
                    return p  # Bu doğru DB — hesaplama verisi burada
            except Exception:
                pass

    # 4) hesaplama_satirlari yoksa: agirlik_hesaplama.db olan ilk konumu kullan
    for loc in candidates:
        p = os.path.join(loc, DB_DOSYA)
        if os.path.exists(p):
            _db_path_cache = p
            return p

    # 5) Hiçbiri yoksa: ilk konumda yeni oluştur
    p = os.path.join(candidates[0], DB_DOSYA)
    _db_path_cache = p
    return p


def get_db_klasor():
    """DB klasörünü döndür"""
    return os.path.dirname(db_path())


def get_conn():
    """
    DB bağlantısı — WAL modu ile lock-safe.
    - WAL: okuma ve yazma birbirini engellemez
    - busy_timeout: kilit varsa 30 saniye bekle (hemen hata atma)
    - synchronous=NORMAL: WAL ile güvenli, daha hızlı
    """
    conn = sqlite3.connect(db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        pass  # DB başka biri tarafından locked olsa bile bağlantı sağlanır
    return conn


def _tablo_kolonlari(conn, tablo):
    try:
        return [r["name"] for r in conn.execute(f"PRAGMA table_info({tablo})")]
    except Exception:
        return []


def _aktif_tablo_bul(conn):
    cur = conn.cursor()
    tablolar = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in TABLO_ADAYLAR:
        if t in tablolar:
            return t
    for t in tablolar:
        if "satir" in t.lower() or "kalem" in t.lower():
            kolonlar = _tablo_kolonlari(conn, t)
            if any("sap" in k.lower() for k in kolonlar):
                return t
    return None


def _kolon_bul(kolonlar, anahtarlar, haric=None):
    haric = haric or []
    for k in kolonlar:
        kl = k.lower()
        if kl in [a.lower() for a in anahtarlar]:
            return k
    for k in kolonlar:
        kl = k.lower()
        if any(h.lower() in kl for h in haric):
            continue
        for a in anahtarlar:
            if a.lower() in kl:
                return k
    return None


def _akilli_kolonlar(kolonlar):
    return {
        "sap_kodu":  _kolon_bul(kolonlar, ["sap_kodu", "sapKodu", "sap_kod", "sap"]),
        "kg":        _kolon_bul(kolonlar, ["kg", "agirlik", "miktar"], haric=["birim", "stok", "sap_stok"]),
        "malzeme":   _kolon_bul(kolonlar, ["malzeme", "mal_grubu", "mg"], haric=["adi", "tanim", "ad"]),
        "kalite":    _kolon_bul(kolonlar, ["kalite", "quality"]),
        "lokasyon":  _kolon_bul(kolonlar, ["lokasyon", "konum"]),
        "olcu":      _kolon_bul(kolonlar, ["olcu", "ölçü", "olcusu"]),
        "birim_kg":  _kolon_bul(kolonlar, ["birim_kg", "birimKg", "birimkg", "kg_birim"]),
        "adet":      _kolon_bul(kolonlar, ["adet", "miktar_adet"]),
        "sayfa_no":  _kolon_bul(kolonlar, ["sayfa_no", "sayfaNo", "sayfa"]),
        "en_std":    _kolon_bul(kolonlar, ["en_std", "enStd", "standart"]),
        "sira_no":   _kolon_bul(kolonlar, ["sira_no", "siraNo", "sira", "siraNumarasi"]),
    }


# ───────────────────────────────────────────────────────────────────────────
# Fallback mal grubu tahmini (MB52'de mal grubu yoksa kullanılır)
# ───────────────────────────────────────────────────────────────────────────

def _mal_grubu_tahmin(malzeme_adi):
    """SAP malzeme adından mal grubu kodu tahmin et (MB52'de mal grubu yoksa)"""
    if not malzeme_adi:
        return None
    ad = str(malzeme_adi).upper()
    if re.search(r"\bKUTU\b", ad) or "KUTU PROF" in ad:    return "MZ-2005"
    if re.search(r"\bSAC\b", ad):                          return "MZ-2006"
    if re.search(r"\bBORU\b", ad):                         return "MZ-2030"
    if "KÖŞEBENT" in ad or "KOSEBENT" in ad:               return "MZ-2002"
    if "NERVÜR" in ad or "NERVUR" in ad:                   return "MZ-2001"
    if "DEMİR DÜZ" in ad or "DUZ DEMIR" in ad:             return "MZ-2076"
    if "LAMA" in ad:                                        return "MZ-2007"
    if "HASIR" in ad:                                       return "MZ-2024"
    if any(k in ad for k in ["PROFİL", "PROFIL", "HEA ", "HEB ", "IPE ", "IPN ", "NPI ", "NPU ", "UPE ", "IPB ", "KİRİŞ", "KIRIŞ"]):
        return "MZ-2003"
    return None


# ───────────────────────────────────────────────────────────────────────────
# TABLO OLUŞTURMA
# ───────────────────────────────────────────────────────────────────────────

def mb52_init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mb52_stoklar (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sap_kodu        TEXT NOT NULL,
            malzeme_adi     TEXT,
            miktar_kg       REAL NOT NULL DEFAULT 0,
            birim           TEXT DEFAULT 'KG',
            depo            TEXT DEFAULT '',
            mal_grubu_kodu  TEXT DEFAULT '',
            mal_grubu_adi   TEXT DEFAULT '',
            yukleme_tarihi  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sap_kodu, depo)
        )
    """)

    # ÖNCE: Eski tablolara eksik kolonları ekle (yoksa)
    for kolon_sql in [
        "ALTER TABLE mb52_stoklar ADD COLUMN mal_grubu_kodu TEXT DEFAULT ''",
        "ALTER TABLE mb52_stoklar ADD COLUMN mal_grubu_adi  TEXT DEFAULT ''"
    ]:
        try: cur.execute(kolon_sql)
        except sqlite3.OperationalError: pass

    # SONRA: Indexleri oluştur (artık kolonlar mevcut)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_mb52_sap ON mb52_stoklar(sap_kodu)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_mb52_mg  ON mb52_stoklar(mal_grubu_kodu)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mb52_yuklemeler (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dosya_adi     TEXT,
            kayit_sayisi  INTEGER DEFAULT 0
        )
    """)

    aktif = _aktif_tablo_bul(conn)
    if aktif:
        try:
            cur.execute(f"ALTER TABLE {aktif} ADD COLUMN sap_stok_kg REAL")
        except sqlite3.OperationalError:
            pass
        # Aktarma id kolonu — hangi aktarmaya ait olduğunu işaretler (geri alma için)
        try:
            cur.execute(f"ALTER TABLE {aktif} ADD COLUMN aktarma_id INTEGER")
        except sqlite3.OperationalError:
            pass
        # Sıra numarası kolonu — sayım anında verilen sayfa içi sıra numarası
        try:
            cur.execute(f"ALTER TABLE {aktif} ADD COLUMN sira_no INTEGER")
            # Eski kayıtlara id değerini sira_no olarak kopyala (initial migration)
            cur.execute(f"UPDATE {aktif} SET sira_no = id WHERE sira_no IS NULL")
        except sqlite3.OperationalError:
            pass

    # Aktarmalar tablosu (geri al için)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mb52_aktarmalar (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            kaynak_sap      TEXT NOT NULL,
            kaynak_malzeme  TEXT,
            hedef_sap       TEXT NOT NULL,
            hedef_malzeme   TEXT,
            hedef_mg_kodu   TEXT,
            satir_sayisi    INTEGER NOT NULL,
            toplam_kg       REAL NOT NULL DEFAULT 0,
            tarih           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            geri_alindi     INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS ix_aktarma_tarih ON mb52_aktarmalar(tarih)")

    # mb52_aktarmalar'a bölme için ek kolonlar
    for kolon_sql in [
        "ALTER TABLE mb52_aktarmalar ADD COLUMN tur TEXT DEFAULT 'aktarma'",
        "ALTER TABLE mb52_aktarmalar ADD COLUMN bolunen_satir_id INTEGER",
        "ALTER TABLE mb52_aktarmalar ADD COLUMN yeni_satir_id INTEGER",
        "ALTER TABLE mb52_aktarmalar ADD COLUMN eski_adet INTEGER",
        "ALTER TABLE mb52_aktarmalar ADD COLUMN eski_kg REAL",
    ]:
        try: cur.execute(kolon_sql)
        except sqlite3.OperationalError: pass

    # Mal grubu manuel sayım durumu (kullanıcının override seçimi)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mal_grubu_durum (
            mal_grubu_kodu     TEXT PRIMARY KEY,
            durum              TEXT NOT NULL,
            guncelleme_tarihi  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ───────────────────────────────────────────────────────────────────────────
# EXCEL PARSE — mal grubu sütununu da yakalar
# ───────────────────────────────────────────────────────────────────────────

def parse_mb52_excel(file_stream):
    if pd is None:
        raise RuntimeError("pandas yüklü değil")

    df = pd.read_excel(file_stream, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    column_map = {}
    for col in df.columns:
        cl = col.lower().replace(".", "").replace(":", "").strip()

        # SAP kodu (mal grubu değil!)
        if "sap_kodu" not in column_map:
            if any(x in cl for x in ["malzeme no", "material no", "matnr"]):
                column_map["sap_kodu"] = col
            elif cl in ("malzeme", "material"):
                column_map["sap_kodu"] = col

        # Mal grubu KODU (MZ-XXXX)
        if "mal_grubu" not in column_map:
            tanim_mi = any(x in cl for x in ["tanım", "tanim", "desc", "açıklama", "aciklama"])
            if not tanim_mi and any(x in cl for x in [
                "mal grubu", "malzeme grubu", "matkl", "material group", "mat grp"
            ]):
                column_map["mal_grubu"] = col

        # Mal grubu TANIMI (opsiyonel — Excel'de varsa direkt al)
        if "mal_grubu_tanim" not in column_map:
            if any(x in cl for x in [
                "mal grubu tanım", "mal grubu tanim", "malzeme grubu tanım",
                "matkl tanim", "material group desc", "mal grubu açıklama"
            ]):
                column_map["mal_grubu_tanim"] = col

        # Malzeme adı (genel açıklama)
        if "malzeme_adi" not in column_map:
            if any(x in cl for x in ["tanım", "tanim", "description", "desc", "kurztext"]):
                if not any(x in cl for x in ["mal grubu", "matkl", "material group"]):
                    column_map["malzeme_adi"] = col

        # Miktar
        if "miktar_kg" not in column_map:
            if any(x in cl for x in ["serbest", "unrestricted", "lbkum", "kullanılabilir", "kullanilabilir"]):
                column_map["miktar_kg"] = col

        # Birim
        if "birim" not in column_map:
            if any(x in cl for x in ["birim", "böb", "bob", "bun", "base unit", "meins"]):
                column_map["birim"] = col

        # Depo
        if "depo" not in column_map:
            if any(x in cl for x in ["depo yeri", "storage loc", "sloc", "lgort"]):
                column_map["depo"] = col

    if "sap_kodu" not in column_map:
        raise ValueError("Excel'de 'Malzeme' sütunu yok. Sütunlar: " + ", ".join(df.columns))
    if "miktar_kg" not in column_map:
        raise ValueError("Excel'de 'Serbest miktar' sütunu yok. Sütunlar: " + ", ".join(df.columns))

    return df, column_map


def _parse_sayi(raw):
    s = str(raw or "0").strip()
    if not s or s.lower() in ("nan", "none"):
        return 0.0
    if "," in s and "." in s:
        if s.rindex(",") > s.rindex("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try: return float(s)
    except (ValueError, TypeError): return 0.0


def _safe_str(val, default=""):
    s = str(val or default).strip()
    if s.lower() in ("nan", "none"): return default
    return s


def _benzerlik(a, b):
    """İki malzeme adı arasında token bazlı benzerlik (0-1) — Jaccard"""
    if not a or not b: return 0.0
    set_a = set(str(a).upper().split())
    set_b = set(str(b).upper().split())
    if not set_a or not set_b: return 0.0
    ortak = set_a & set_b
    toplam = set_a | set_b
    return len(ortak) / len(toplam) if toplam else 0.0


# ───────────────────────────────────────────────────────────────────────────
# ROUTE: MB52 EXCEL YÜKLE
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/yukle", methods=["POST"])
def mb52_yukle():
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya gönderilmedi"}), 400
    f = request.files["dosya"]
    if not f.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya boş"}), 400

    try:
        df, col_map = parse_mb52_excel(f.stream)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 400

    has_mal_grubu = "mal_grubu" in col_map

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM mb52_stoklar")

    eklenen = 0
    atlanan = 0
    eslenmeyen_mg = set()  # Bilinmeyen mal grubu kodları (rapor için)
    simdi = datetime.utcnow().isoformat()

    for _, row in df.iterrows():
        sap_kodu = _safe_str(row[col_map["sap_kodu"]])
        if not sap_kodu:
            atlanan += 1
            continue

        miktar = _parse_sayi(row[col_map["miktar_kg"]])
        ad     = _safe_str(row[col_map["malzeme_adi"]] if "malzeme_adi" in col_map else "")
        birim  = _safe_str(row[col_map["birim"]] if "birim" in col_map else "", default="KG")
        depo   = _safe_str(row[col_map["depo"]]  if "depo"  in col_map else "")

        # Mal grubu: önce Excel'den, yoksa malzeme adından tahmin
        mg_kodu = ""
        mg_adi  = ""
        if has_mal_grubu:
            mg_kodu = _safe_str(row[col_map["mal_grubu"]]).upper()

        if not mg_kodu:
            # Excel'de yok — malzeme adından tahmin et
            tahmin = _mal_grubu_tahmin(ad)
            if tahmin:
                mg_kodu = tahmin

        # Mal grubu adı: önce Excel'den (varsa), sonra sözlükten
        if "mal_grubu_tanim" in col_map:
            mg_adi = _safe_str(row[col_map["mal_grubu_tanim"]])
        if not mg_adi and mg_kodu:
            mg_adi = mal_grubu_tanimi(mg_kodu) or ""
        if mg_kodu and not mg_adi:
            eslenmeyen_mg.add(mg_kodu)
            mg_adi = mg_kodu  # Tanım yoksa kodu göster

        try:
            cur.execute("""
                INSERT INTO mb52_stoklar
                    (sap_kodu, malzeme_adi, miktar_kg, birim, depo, mal_grubu_kodu, mal_grubu_adi, yukleme_tarihi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sap_kodu, depo) DO UPDATE SET
                    miktar_kg      = excluded.miktar_kg,
                    malzeme_adi    = excluded.malzeme_adi,
                    birim          = excluded.birim,
                    mal_grubu_kodu = excluded.mal_grubu_kodu,
                    mal_grubu_adi  = excluded.mal_grubu_adi,
                    yukleme_tarihi = excluded.yukleme_tarihi
            """, (sap_kodu, ad, miktar, birim, depo, mg_kodu, mg_adi, simdi))
            eklenen += 1
        except Exception:
            atlanan += 1

    cur.execute("INSERT INTO mb52_yuklemeler (dosya_adi, kayit_sayisi) VALUES (?, ?)",
                (f.filename, eklenen))
    conn.commit()
    conn.close()

    mesaj = f"{eklenen} malzeme yüklendi"
    if atlanan: mesaj += f", {atlanan} atlandı"
    if not has_mal_grubu:
        mesaj += ". Excel'de mal grubu sütunu yok — malzeme adından tahmin yapıldı."
    elif eslenmeyen_mg:
        mesaj += f". {len(eslenmeyen_mg)} bilinmeyen mal grubu kodu var: {', '.join(sorted(eslenmeyen_mg)[:5])}"

    return jsonify({
        "durum":   "ok",
        "mesaj":   mesaj,
        "eklenen": eklenen,
        "atlanan": atlanan,
        "mal_grubu_sutunu": has_mal_grubu,
        "eslenmeyen_mg":    sorted(list(eslenmeyen_mg))[:20]
    })


# ───────────────────────────────────────────────────────────────────────────
# ROUTE: BİR SAP KODUNUN STOĞU (mal grubu da döner)
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/stok/<path:sap_kodu>", methods=["GET"])
def mb52_stok(sap_kodu):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT sap_kodu,
               MAX(malzeme_adi) AS malzeme_adi,
               SUM(miktar_kg)   AS miktar_kg,
               MAX(birim)       AS birim,
               MAX(mal_grubu_kodu) AS mg_kodu,
               MAX(mal_grubu_adi)  AS mg_adi,
               COUNT(*)         AS depo_sayisi
        FROM mb52_stoklar WHERE sap_kodu = ? GROUP BY sap_kodu
    """, (sap_kodu.strip(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"durum": "yok", "sap_kodu": sap_kodu, "miktar_kg": 0})
    return jsonify({
        "durum":          "ok",
        "sap_kodu":       row["sap_kodu"],
        "malzeme_adi":    row["malzeme_adi"] or "",
        "miktar_kg":      round(row["miktar_kg"] or 0, 2),
        "birim":          row["birim"] or "KG",
        "mal_grubu_kodu": row["mg_kodu"] or "",
        "mal_grubu_adi":  (mal_grubu_tanimi(row["mg_kodu"]) if row["mg_kodu"] else None) or row["mg_adi"] or "",
        "depo_sayisi":    row["depo_sayisi"]
    })


# ───────────────────────────────────────────────────────────────────────────
# ROUTE: MB52 DURUM
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/durum", methods=["GET"])
def mb52_durum():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT sap_kodu) AS adet, MAX(yukleme_tarihi) AS son FROM mb52_stoklar")
    row = cur.fetchone()
    # Mal grubu kapsama oranı
    cur.execute("SELECT COUNT(*) FROM mb52_stoklar WHERE mal_grubu_kodu != ''")
    mg_dolu = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mb52_stoklar")
    toplam = cur.fetchone()[0]
    conn.close()
    return jsonify({
        "kayit_sayisi":      row["adet"] or 0,
        "son_yukleme":       row["son"]  or None,
        "db_dosyasi":        DB_DOSYA,
        "mg_kapsama_yuzde":  round(mg_dolu * 100 / toplam, 1) if toplam else 0
    })


# ───────────────────────────────────────────────────────────────────────────
# ROUTE: FARK RAPORU — Mal grubu artık MB52'den
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/sayim-fark", methods=["GET"])
def mb52_sayim_fark():
    filtre = request.args.get("filtre", "tum")

    conn = get_conn()
    cur = conn.cursor()

    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({
            "durum": "hata", "mesaj": "Hesaplama tablosu bulunamadı",
            "ozet": {"toplam":0,"tamam":0,"eksik":0,"fazla":0,"sayilmadi":0,"sapsiz":0},
            "mal_grubu_ozet": [], "sonuclar": [], "sapsiz_sonuclar": []
        })

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    kol = _akilli_kolonlar(mevcut_kolonlar)
    if not kol["sap_kodu"] or not kol["kg"]:
        conn.close()
        return jsonify({
            "durum": "hata", "mesaj": f"sap_kodu/kg kolonu yok. Kolonlar: {mevcut_kolonlar}",
            "ozet": {"toplam":0,"tamam":0,"eksik":0,"fazla":0,"sayilmadi":0,"sapsiz":0},
            "mal_grubu_ozet": [], "sonuclar": [], "sapsiz_sonuclar": []
        })

    # 1) Sayım toplamlarını SAP koduna göre topla
    select_parts = [
        f"{kol['sap_kodu']} AS sap_kodu",
        f"SUM({kol['kg']}) AS sayim_kg",
        "COUNT(*) AS satir_sayisi"
    ]
    if kol["kalite"]:   select_parts.append(f"MAX({kol['kalite']}) AS kalite")
    if kol["lokasyon"]: select_parts.append(f"GROUP_CONCAT(DISTINCT {kol['lokasyon']}) AS lokasyonlar")

    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM {aktif_tablo}
        WHERE {kol['sap_kodu']} IS NOT NULL AND TRIM({kol['sap_kodu']}) != ''
        GROUP BY {kol['sap_kodu']}
    """
    try:
        cur.execute(sql)
        sayim_dict = {r["sap_kodu"]: dict(r) for r in cur.fetchall()}
    except sqlite3.OperationalError as e:
        conn.close()
        return jsonify({
            "durum": "hata", "mesaj": f"SQL: {e}",
            "ozet": {"toplam":0,"tamam":0,"eksik":0,"fazla":0,"sayilmadi":0,"sapsiz":0},
            "mal_grubu_ozet": [], "sonuclar": [], "sapsiz_sonuclar": []
        })

    # 2) MB52 verilerini al (mal grubu dahil)
    cur.execute("""
        SELECT sap_kodu,
               MAX(malzeme_adi)    AS malzeme_adi,
               SUM(miktar_kg)      AS miktar_kg,
               MAX(birim)          AS birim,
               MAX(mal_grubu_kodu) AS mg_kodu,
               MAX(mal_grubu_adi)  AS mg_adi
        FROM mb52_stoklar GROUP BY sap_kodu
    """)
    mb52_dict = {r["sap_kodu"]: dict(r) for r in cur.fetchall()}
    conn.close()

    # 3) ANA LİSTE — MB52 baz
    sonuclar = []
    for sap, m in mb52_dict.items():
        sap_kg = m["miktar_kg"] or 0
        sayim_data = sayim_dict.get(sap)

        mg_kodu = m["mg_kodu"] or ""
        # ÖNCE dict'ten al (her zaman güncel), YOKSA DB snapshot'undan
        mg_adi  = (mal_grubu_tanimi(mg_kodu) if mg_kodu else None) or m["mg_adi"] or ""
        if not mg_kodu:
            mg_kodu = "BILINMIYOR"
            mg_adi  = "Bilinmiyor"

        if sayim_data is None:
            durum = "sayilmadi"; sayim_kg = None; fark = None
            satir_sayisi = 0; lokasyonlar = ""
        else:
            sayim_kg = sayim_data["sayim_kg"] or 0
            fark = sayim_kg - sap_kg
            durum = "tamam" if abs(fark) < 0.5 else ("eksik" if fark < 0 else "fazla")
            satir_sayisi = sayim_data["satir_sayisi"] or 0
            lokasyonlar = sayim_data.get("lokasyonlar") or ""

        sonuclar.append({
            "sap_kodu":       sap,
            "malzeme_adi":    m["malzeme_adi"] or "",
            "mal_grubu_kodu": mg_kodu,
            "mal_grubu_adi":  mg_adi,
            "sap_kg":         round(sap_kg, 2),
            "sayim_kg":       round(sayim_kg, 2) if sayim_kg is not None else None,
            "fark_kg":        round(fark, 2) if fark is not None else None,
            "satir_sayisi":   satir_sayisi,
            "lokasyonlar":    lokasyonlar,
            "durum":          durum
        })

    # 4) SAP'ta yok — sayımda var ama MB52'de yok
    sapsiz_sonuclar = []
    for sap, s in sayim_dict.items():
        if sap not in mb52_dict:
            sapsiz_sonuclar.append({
                "sap_kodu":       sap,
                "malzeme_adi":    s.get("kalite") or "",
                "mal_grubu_kodu": "BILINMIYOR",
                "mal_grubu_adi":  "Bilinmiyor",
                "sap_kg":         0,
                "sayim_kg":       round(s["sayim_kg"] or 0, 2),
                "fark_kg":        round(s["sayim_kg"] or 0, 2),
                "satir_sayisi":   s["satir_sayisi"] or 0,
                "lokasyonlar":    s.get("lokasyonlar") or "",
                "durum":          "sapsiz"
            })

    # 5) Mal grubu kümülasyonu (kod bazlı)
    mg_dict = {}
    def mg_ekle(s, sapsiz_mi=False):
        k = s["mal_grubu_kodu"] or "BILINMIYOR"
        if k not in mg_dict:
            # Önce dict'ten al — kullanıcı kodu güncellediğinde yansır
            ad = mal_grubu_tanimi(k) or s["mal_grubu_adi"] or k
            mg_dict[k] = {
                "mal_grubu_kodu": k,
                "mal_grubu_adi":  ad,
                "sap_kod_sayisi": 0, "sayilmis_kod": 0, "sayilmamis_kod": 0, "sapsiz_kod": 0,
                "satir_sayisi": 0, "sap_kg": 0.0, "sayim_kg": 0.0,
                "tamam":0,"eksik":0,"fazla":0,"sayilmadi":0,"sapsiz":0
            }
        d = mg_dict[k]
        d["sap_kod_sayisi"] += 1
        d["satir_sayisi"]   += s.get("satir_sayisi", 0)
        d["sap_kg"]         += s.get("sap_kg", 0) or 0
        if s.get("sayim_kg") is not None:
            d["sayim_kg"]   += s["sayim_kg"]
        if sapsiz_mi:
            d["sapsiz_kod"] += 1; d["sapsiz"] += 1
        elif s["durum"] == "sayilmadi":
            d["sayilmamis_kod"] += 1; d["sayilmadi"] += 1
        else:
            d["sayilmis_kod"] += 1; d[s["durum"]] += 1

    for s in sonuclar:        mg_ekle(s, False)
    for s in sapsiz_sonuclar: mg_ekle(s, True)

    mal_grubu_ozet = []

    # Manuel override'ları topla — kullanıcı bazı mal gruplarını manuel işaretlemiş olabilir
    try:
        cur2 = conn.cursor() if False else None  # placeholder, conn aşağıda zaten kapalı
    except: pass

    # conn yukarıda kapalıydı, yeni bağlantı aç
    conn2 = get_conn()
    try:
        rows_md = conn2.execute("SELECT mal_grubu_kodu, durum FROM mal_grubu_durum").fetchall()
        manuel_durum = {r["mal_grubu_kodu"]: r["durum"] for r in rows_md}
    except sqlite3.OperationalError:
        manuel_durum = {}
    finally:
        conn2.close()

    for m in mg_dict.values():
        m["sap_kg"]   = round(m["sap_kg"],   2)
        m["sayim_kg"] = round(m["sayim_kg"], 2)
        m["fark_kg"]  = round(m["sayim_kg"] - m["sap_kg"], 2)
        if abs(m["fark_kg"]) < 0.5:        m["genel_durum"] = "tamam"
        elif m["fark_kg"] < 0:             m["genel_durum"] = "eksik"
        else:                              m["genel_durum"] = "fazla"

        # Otomatik sayım ilerleme durumu
        toplam_sap_kod = (m["sayilmis_kod"] or 0) + (m["sayilmamis_kod"] or 0)
        if toplam_sap_kod == 0:
            otomatik_durum = "sayilmadi"
            otomatik_yuzde = 0
        elif (m["sayilmamis_kod"] or 0) == 0:
            otomatik_durum = "tamamlandi"
            otomatik_yuzde = 100
        elif (m["sayilmis_kod"] or 0) == 0:
            otomatik_durum = "sayilmadi"
            otomatik_yuzde = 0
        else:
            otomatik_durum = "devam_ediyor"
            otomatik_yuzde = round(100 * m["sayilmis_kod"] / toplam_sap_kod)

        # Manuel override varsa onu kullan, yoksa otomatiği kullan
        mg_kodu_iter = m["mal_grubu_kodu"]
        if mg_kodu_iter in manuel_durum:
            m["sayim_durumu"]        = manuel_durum[mg_kodu_iter]
            m["sayim_durumu_manuel"] = True
            m["sayim_yuzde"]         = otomatik_yuzde  # bilgi amaçlı yine de gönder
        else:
            m["sayim_durumu"]        = otomatik_durum
            m["sayim_durumu_manuel"] = False
            m["sayim_yuzde"]         = otomatik_yuzde

        mal_grubu_ozet.append(m)

    mal_grubu_ozet.sort(key=lambda x: x["sap_kg"], reverse=True)

    ozet = {
        "toplam":     len(mb52_dict),
        "tamam":      sum(1 for s in sonuclar if s["durum"] == "tamam"),
        "eksik":      sum(1 for s in sonuclar if s["durum"] == "eksik"),
        "fazla":      sum(1 for s in sonuclar if s["durum"] == "fazla"),
        "sayilmadi":  sum(1 for s in sonuclar if s["durum"] == "sayilmadi"),
        "sapsiz":     len(sapsiz_sonuclar)
    }

    if filtre and filtre != "tum":
        sonuclar = [s for s in sonuclar if s["durum"] == filtre]

    return jsonify({
        "ozet":            ozet,
        "mal_grubu_ozet":  mal_grubu_ozet,
        "sonuclar":        sonuclar,
        "sapsiz_sonuclar": sapsiz_sonuclar,
        "aktif_tablo":     aktif_tablo
    })


# ───────────────────────────────────────────────────────────────────────────
# ROUTE: SAP detay
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/sayim-detay/<path:sap_kodu>", methods=["GET"])
def mb52_sayim_detay(sap_kodu):
    conn = get_conn()
    cur = conn.cursor()
    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yok", "satirlar": []})

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    kol = _akilli_kolonlar(mevcut_kolonlar)
    sap_c = kol["sap_kodu"]; kg_c = kol["kg"]
    if not sap_c or not kg_c:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "kolon yok", "satirlar": []})

    select_kols = list(set(filter(None, [
        "id" if "id" in mevcut_kolonlar else None,
        sap_c, kg_c, kol["malzeme"], kol["kalite"], kol["lokasyon"],
        kol["olcu"], kol["birim_kg"], kol["adet"], kol["sayfa_no"], kol["en_std"],
        kol["sira_no"],
        "aktarma_id" if "aktarma_id" in mevcut_kolonlar else None,
    ])))
    sql = f"SELECT {', '.join(select_kols)} FROM {aktif_tablo} WHERE {sap_c} = ? ORDER BY id"

    try:
        cur.execute(sql, (sap_kodu.strip(),))
        rows_raw = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError as e:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": str(e), "satirlar": []})

    rows = []
    for r in rows_raw:
        rows.append({
            "id":         r.get("id"),
            "sap_kodu":   r.get(sap_c),
            "kg":         r.get(kg_c) or 0,
            "malzeme":    r.get(kol["malzeme"], "") if kol["malzeme"] else "",
            "kalite":     r.get(kol["kalite"], "")  if kol["kalite"]  else "",
            "lokasyon":   r.get(kol["lokasyon"], "") if kol["lokasyon"] else "",
            "olcu":       r.get(kol["olcu"], "")     if kol["olcu"]     else "",
            "birim_kg":   r.get(kol["birim_kg"], 0)  if kol["birim_kg"] else 0,
            "adet":       r.get(kol["adet"], 0)      if kol["adet"]     else 0,
            "sayfa_no":   r.get(kol["sayfa_no"], "") if kol["sayfa_no"] else "",
            "sira_no":    r.get(kol["sira_no"])      if kol["sira_no"]  else None,
            "aktarma_id": r.get("aktarma_id"),
        })

    cur.execute("""
        SELECT SUM(miktar_kg) AS m, MAX(malzeme_adi) AS adi,
               MAX(mal_grubu_kodu) AS mg_kodu, MAX(mal_grubu_adi) AS mg_adi
        FROM mb52_stoklar WHERE sap_kodu = ?
    """, (sap_kodu.strip(),))
    sap_row = cur.fetchone()
    sap_kg  = (sap_row["m"] if sap_row else 0) or 0
    sap_adi = (sap_row["adi"] if sap_row else "") or ""
    mg_kodu = (sap_row["mg_kodu"] if sap_row else "") or ""
    # Önce dict'ten al (her zaman güncel), yoksa DB'deki snapshot
    mg_adi  = (mal_grubu_tanimi(mg_kodu) if mg_kodu else None) or (sap_row["mg_adi"] if sap_row else "") or ""
    conn.close()

    toplam_sayim = sum((r.get("kg") or 0) for r in rows)
    fark = toplam_sayim - sap_kg

    return jsonify({
        "durum":          "ok",
        "sap_kodu":       sap_kodu,
        "malzeme_adi":    sap_adi,
        "mal_grubu_kodu": mg_kodu,
        "mal_grubu_adi":  mg_adi,
        "toplam_sayim":   round(toplam_sayim, 2),
        "sap_kg":         round(sap_kg, 2),
        "fark_kg":        round(fark, 2),
        "satir_sayisi":   len(rows),
        "satirlar":       rows
    })


# ───────────────────────────────────────────────────────────────────────────
# DEBUG
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/debug", methods=["GET"])
def mb52_debug():
    conn = get_conn()
    cur = conn.cursor()
    tablolar = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    tablo_kolonlari = {t: _tablo_kolonlari(conn, t) for t in tablolar}
    aktif_tablo = _aktif_tablo_bul(conn)
    sonuc = {
        "db_path": db_path(), "tablolar": tablolar,
        "tablo_kolonlari": tablo_kolonlari, "aktif_tablo": aktif_tablo,
        "mal_grubu_sozluk_adet": len(MAL_GRUBU_ADLARI)
    }
    if aktif_tablo:
        kol = _akilli_kolonlar(tablo_kolonlari[aktif_tablo])
        sonuc["tespit_edilen_kolonlar"] = kol
        try:
            cur.execute(f"SELECT COUNT(*) FROM {aktif_tablo}")
            sonuc["satir_sayisi"] = cur.fetchone()[0]
            if kol["sap_kodu"]:
                cur.execute(f"SELECT COUNT(*) FROM {aktif_tablo} WHERE {kol['sap_kodu']} IS NOT NULL AND TRIM({kol['sap_kodu']}) != ''")
                sonuc["sap_kodlu_satir"] = cur.fetchone()[0]
        except Exception as e:
            sonuc["hata"] = str(e)
    cur.execute("SELECT COUNT(*) FROM mb52_stoklar")
    sonuc["mb52_stoklar_satir"] = cur.fetchone()[0]
    # MB52 mal grubu istatistiği
    cur.execute("SELECT mal_grubu_kodu, COUNT(*) as adet FROM mb52_stoklar WHERE mal_grubu_kodu != '' GROUP BY mal_grubu_kodu ORDER BY adet DESC LIMIT 10")
    sonuc["top_mal_gruplari"] = [{"kod": r[0], "adi": MAL_GRUBU_ADLARI.get(r[0], "?"), "adet": r[1]} for r in cur.fetchall()]
    conn.close()
    return jsonify(sonuc)


# ───────────────────────────────────────────────────────────────────────────
# AKTARMA SİSTEMİ — Sayım satırlarını farklı SAP koduna taşı
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/oneri/<path:sap_kodu>", methods=["GET"])
def mb52_oneri(sap_kodu):
    """
    Bir SAP kodunun benzer alternatiflerini öner (aktarma için).
    Aynı mal grubu içinde malzeme adı benzerliğine göre top N.
    """
    limit = int(request.args.get("limit") or 8)
    conn = get_conn()
    cur = conn.cursor()

    # Kaynak SAP'ın bilgileri
    cur.execute("""
        SELECT MAX(malzeme_adi) AS adi, MAX(mal_grubu_kodu) AS mg
        FROM mb52_stoklar WHERE sap_kodu = ?
    """, (sap_kodu.strip(),))
    kaynak = cur.fetchone()
    if not kaynak or not kaynak["adi"]:
        conn.close()
        return jsonify({"durum": "yok", "oneriler": []})

    kaynak_adi = kaynak["adi"]
    kaynak_mg  = kaynak["mg"] or ""

    # Aynı mal grubundaki diğer SAP kodlarını al
    if kaynak_mg:
        cur.execute("""
            SELECT sap_kodu, MAX(malzeme_adi) AS adi, SUM(miktar_kg) AS kg
            FROM mb52_stoklar
            WHERE mal_grubu_kodu = ? AND sap_kodu != ?
            GROUP BY sap_kodu
        """, (kaynak_mg, sap_kodu.strip()))
    else:
        # Mal grubu yoksa: tüm MB52 (yavaş olabilir ama nadir)
        cur.execute("""
            SELECT sap_kodu, MAX(malzeme_adi) AS adi, SUM(miktar_kg) AS kg
            FROM mb52_stoklar WHERE sap_kodu != ?
            GROUP BY sap_kodu LIMIT 500
        """, (sap_kodu.strip(),))

    adaylar = [dict(r) for r in cur.fetchall()]

    # Her aday için benzerlik hesapla
    for a in adaylar:
        a["benzerlik"] = round(_benzerlik(kaynak_adi, a["adi"]), 3)

    # Benzerliğe göre sırala ve top N al
    adaylar.sort(key=lambda x: x["benzerlik"], reverse=True)
    oneriler = adaylar[:limit]

    conn.close()
    return jsonify({
        "durum": "ok",
        "kaynak_sap": sap_kodu,
        "kaynak_adi": kaynak_adi,
        "oneriler": [{
            "sap_kodu":    o["sap_kodu"],
            "malzeme_adi": o["adi"] or "",
            "mb52_kg":     round(o["kg"] or 0, 2),
            "benzerlik":   o["benzerlik"],
            "yuzde":       int(o["benzerlik"] * 100)
        } for o in oneriler]
    })


@mb52_bp.route("/api/mb52/aktarma-yap", methods=["POST"])
def mb52_aktarma_yap():
    """
    Seçili satırları başka SAP koduna taşı.
    Body: {kaynak_sap, hedef_sap, satir_ids: [int, ...]}
    """
    d = request.get_json(silent=True) or {}
    kaynak_sap = (d.get("kaynak_sap") or "").strip()
    hedef_sap  = (d.get("hedef_sap")  or "").strip()
    satir_ids  = d.get("satir_ids") or []

    if not kaynak_sap or not hedef_sap:
        return jsonify({"durum": "hata", "mesaj": "Kaynak/hedef SAP boş"}), 400
    if kaynak_sap == hedef_sap:
        return jsonify({"durum": "hata", "mesaj": "Aynı SAP koduna aktarılamaz"}), 400
    if not satir_ids or not isinstance(satir_ids, list):
        return jsonify({"durum": "hata", "mesaj": "Satır seçilmedi"}), 400

    conn = get_conn()
    cur = conn.cursor()
    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Hesaplama tablosu yok"}), 500

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    kol = _akilli_kolonlar(mevcut_kolonlar)
    sap_c, kg_c = kol["sap_kodu"], kol["kg"]

    if not sap_c or not kg_c or "aktarma_id" not in mevcut_kolonlar:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yapısı uygun değil"}), 500

    # Seçili satırları çek
    placeholders = ",".join(["?"] * len(satir_ids))
    cur.execute(f"""
        SELECT id, {sap_c} AS sap, {kg_c} AS kg
        FROM {aktif_tablo}
        WHERE id IN ({placeholders}) AND {sap_c} = ?
    """, satir_ids + [kaynak_sap])
    satirlar = [dict(r) for r in cur.fetchall()]

    if not satirlar:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Eşleşen satır yok (kaynak SAP ile satır id'leri uyuşmuyor olabilir)"}), 400

    toplam_kg = sum((s["kg"] or 0) for s in satirlar)

    # MB52'den kaynak ve hedef malzeme adlarını al
    cur.execute("SELECT MAX(malzeme_adi) AS adi FROM mb52_stoklar WHERE sap_kodu = ?", (kaynak_sap,))
    kaynak_adi = (cur.fetchone()["adi"] or "")
    cur.execute("SELECT MAX(malzeme_adi) AS adi, MAX(mal_grubu_kodu) AS mg FROM mb52_stoklar WHERE sap_kodu = ?", (hedef_sap,))
    hedef_row = cur.fetchone()
    hedef_adi = (hedef_row["adi"] if hedef_row else "") or ""
    hedef_mg  = (hedef_row["mg"]  if hedef_row else "") or ""

    # Aktarma kaydı oluştur
    cur.execute("""
        INSERT INTO mb52_aktarmalar
            (kaynak_sap, kaynak_malzeme, hedef_sap, hedef_malzeme, hedef_mg_kodu, satir_sayisi, toplam_kg)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (kaynak_sap, kaynak_adi, hedef_sap, hedef_adi, hedef_mg, len(satirlar), toplam_kg))
    aktarma_id = cur.lastrowid

    # Satırların sap_kodu'nu hedef_sap olarak değiştir + aktarma_id işaretle
    cur.executemany(
        f"UPDATE {aktif_tablo} SET {sap_c} = ?, aktarma_id = ? WHERE id = ?",
        [(hedef_sap, aktarma_id, s["id"]) for s in satirlar]
    )

    conn.commit()
    conn.close()

    return jsonify({
        "durum":        "ok",
        "mesaj":        f"{len(satirlar)} satır aktarıldı ({toplam_kg:,.2f} kg)",
        "aktarma_id":   aktarma_id,
        "satir_sayisi": len(satirlar),
        "toplam_kg":    round(toplam_kg, 2)
    })


@mb52_bp.route("/api/mb52/aktarmalar", methods=["GET"])
def mb52_aktarmalar():
    """Tüm aktif (geri alınmamış) aktarmaları listele"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM mb52_aktarmalar
        WHERE geri_alindi = 0
        ORDER BY tarih DESC
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"aktarmalar": rows, "adet": len(rows)})


@mb52_bp.route("/api/mb52/aktarma-geri-al/<int:aktarma_id>", methods=["POST"])
def mb52_aktarma_geri_al(aktarma_id):
    """Bir aktarmayı geri al — tür bölme veya aktarma olabilir"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mb52_aktarmalar WHERE id = ?", (aktarma_id,))
    a = cur.fetchone()
    if not a:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Aktarma bulunamadı"}), 404
    if a["geri_alindi"]:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Bu aktarma zaten geri alınmış"}), 400

    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yok"}), 500

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    kol = _akilli_kolonlar(mevcut_kolonlar)
    sap_c = kol["sap_kodu"]
    if not sap_c or "aktarma_id" not in mevcut_kolonlar:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yapısı uygun değil"}), 500

    a_dict = dict(a)
    tur = a_dict.get("tur") or "aktarma"
    etkilenen = 0
    mesaj = ""

    if tur == "bolme":
        # Bölme geri alma:
        # 1) Yeni oluşturulan satırı sil
        if a_dict.get("yeni_satir_id"):
            cur.execute(f"DELETE FROM {aktif_tablo} WHERE id = ?", (a_dict["yeni_satir_id"],))
        # 2) Bölünen satırı eski adet/kg'a döndür
        if a_dict.get("bolunen_satir_id") and a_dict.get("eski_adet") is not None and a_dict.get("eski_kg") is not None:
            cur.execute(
                f"UPDATE {aktif_tablo} SET {kol['adet']} = ?, {kol['kg']} = ?, aktarma_id = NULL WHERE id = ?",
                (a_dict["eski_adet"], a_dict["eski_kg"], a_dict["bolunen_satir_id"])
            )
            etkilenen = 1
        mesaj = f"Bölme iptal edildi — {a_dict['kaynak_sap']} satırı eski haline döndürüldü"
    else:
        # Standart aktarma geri alma
        cur.execute(
            f"UPDATE {aktif_tablo} SET {sap_c} = ?, aktarma_id = NULL WHERE aktarma_id = ?",
            (a_dict["kaynak_sap"], aktarma_id)
        )
        etkilenen = cur.rowcount
        mesaj = f"{etkilenen} satır {a_dict['kaynak_sap']} koduna geri döndürüldü"

    # Aktarmayı geri_alindi olarak işaretle
    cur.execute("UPDATE mb52_aktarmalar SET geri_alindi = 1 WHERE id = ?", (aktarma_id,))

    conn.commit()
    conn.close()
    return jsonify({
        "durum": "ok",
        "mesaj": mesaj,
        "etkilenen_satir": etkilenen,
        "tur": tur
    })


# ───────────────────────────────────────────────────────────────────────────
# SATIR BÖLME — Bir satırın belirli adetini başka SAP koduna ayır
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/satir-bol", methods=["POST"])
def mb52_satir_bol():
    """
    Bir satırı iki parçaya böl. Eski satırın adeti azalır, yeni satır oluşur.
    Body: {satir_id, ayrilacak_adet, hedef_sap}

    Örnek: 27 adet C255 → 6 adetini C355'e ayır
      - Eski satır: 21 adet C255 (kalan)
      - Yeni satır: 6 adet C355 (aynı sira_no, aynı diğer alanlar)
    """
    d = request.get_json(silent=True) or {}
    satir_id = d.get("satir_id")
    ayrilacak_adet = d.get("ayrilacak_adet")
    hedef_sap = (d.get("hedef_sap") or "").strip()

    if not satir_id:
        return jsonify({"durum": "hata", "mesaj": "satir_id gerekli"}), 400
    if not hedef_sap:
        return jsonify({"durum": "hata", "mesaj": "Hedef SAP kodu boş olamaz"}), 400

    try:
        ayrilacak_adet = int(ayrilacak_adet)
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "Ayrılacak adet sayı olmalı"}), 400

    if ayrilacak_adet <= 0:
        return jsonify({"durum": "hata", "mesaj": "Ayrılacak adet 0'dan büyük olmalı"}), 400

    conn = get_conn()
    cur = conn.cursor()
    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yok"}), 500

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    kol = _akilli_kolonlar(mevcut_kolonlar)

    if not kol["sap_kodu"] or not kol["kg"] or not kol["adet"] or not kol["birim_kg"]:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Gerekli kolonlar (sap_kodu/kg/adet/birim_kg) yok"}), 500

    # Satırı çek
    cur.execute(f"SELECT * FROM {aktif_tablo} WHERE id = ?", (satir_id,))
    satir = cur.fetchone()
    if not satir:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Satır bulunamadı"}), 404

    satir_dict = dict(satir)
    eski_adet = satir_dict.get(kol["adet"]) or 0
    eski_kg   = satir_dict.get(kol["kg"]) or 0
    birim_kg  = satir_dict.get(kol["birim_kg"]) or 0
    kaynak_sap = satir_dict.get(kol["sap_kodu"]) or ""

    try:
        eski_adet = int(eski_adet)
    except (TypeError, ValueError):
        eski_adet = 0

    if ayrilacak_adet >= eski_adet:
        conn.close()
        return jsonify({
            "durum": "hata",
            "mesaj": f"Ayrılacak adet ({ayrilacak_adet}) mevcut adetten ({eski_adet}) küçük olmalı"
        }), 400

    if kaynak_sap == hedef_sap:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Aynı SAP koduna bölünemez"}), 400

    # Hesaplamalar
    yeni_adet = ayrilacak_adet
    yeni_kg = round(birim_kg * yeni_adet, 2)
    kalan_adet = eski_adet - ayrilacak_adet
    kalan_kg = round(birim_kg * kalan_adet, 2)

    # MB52'den hedef bilgilerini al
    cur.execute("SELECT MAX(malzeme_adi) AS adi FROM mb52_stoklar WHERE sap_kodu = ?", (kaynak_sap,))
    kaynak_adi_row = cur.fetchone()
    kaynak_adi = (kaynak_adi_row["adi"] if kaynak_adi_row else "") or ""

    cur.execute("SELECT MAX(malzeme_adi) AS adi, MAX(mal_grubu_kodu) AS mg FROM mb52_stoklar WHERE sap_kodu = ?", (hedef_sap,))
    hedef_row = cur.fetchone()
    hedef_adi = (hedef_row["adi"] if hedef_row else "") or ""
    hedef_mg  = (hedef_row["mg"]  if hedef_row else "") or ""

    # mb52_aktarmalar kaydı oluştur (tur='bolme')
    cur.execute("""
        INSERT INTO mb52_aktarmalar
            (tur, kaynak_sap, kaynak_malzeme, hedef_sap, hedef_malzeme, hedef_mg_kodu,
             satir_sayisi, toplam_kg, bolunen_satir_id, eski_adet, eski_kg)
        VALUES ('bolme', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
    """, (kaynak_sap, kaynak_adi, hedef_sap, hedef_adi, hedef_mg,
          yeni_kg, satir_id, eski_adet, eski_kg))
    aktarma_id = cur.lastrowid

    # Eski satırı güncelle (adet ve kg azalt)
    cur.execute(
        f"UPDATE {aktif_tablo} SET {kol['adet']} = ?, {kol['kg']} = ? WHERE id = ?",
        (kalan_adet, kalan_kg, satir_id)
    )

    # Yeni satır oluştur — tüm alanlar kopyalanır, sadece sap_kodu/adet/kg/aktarma_id değişir
    kolon_isimleri = [k for k in satir_dict.keys() if k != "id"]
    yeni_degerler = []
    for k in kolon_isimleri:
        if k == kol["sap_kodu"]:
            yeni_degerler.append(hedef_sap)
        elif k == kol["adet"]:
            yeni_degerler.append(yeni_adet)
        elif k == kol["kg"]:
            yeni_degerler.append(yeni_kg)
        elif k == "aktarma_id":
            yeni_degerler.append(aktarma_id)
        else:
            yeni_degerler.append(satir_dict.get(k))

    placeholders = ",".join(["?"] * len(kolon_isimleri))
    cur.execute(
        f"INSERT INTO {aktif_tablo} ({','.join(kolon_isimleri)}) VALUES ({placeholders})",
        yeni_degerler
    )
    yeni_satir_id = cur.lastrowid

    # Aktarma kaydını yeni_satir_id ile güncelle
    cur.execute("UPDATE mb52_aktarmalar SET yeni_satir_id = ? WHERE id = ?", (yeni_satir_id, aktarma_id))

    conn.commit()
    conn.close()

    return jsonify({
        "durum": "ok",
        "mesaj": f"{ayrilacak_adet} adet ({yeni_kg:.2f} kg) {hedef_sap} koduna ayrıldı",
        "aktarma_id": aktarma_id,
        "kaynak": {"adet": kalan_adet, "kg": kalan_kg, "sap": kaynak_sap},
        "hedef":  {"adet": yeni_adet, "kg": yeni_kg, "sap": hedef_sap, "satir_id": yeni_satir_id}
    })


# ───────────────────────────────────────────────────────────────────────────
# SIRA NUMARASI GÜNCELLEME — manuel düzenleme
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/satir-sira-guncelle/<int:satir_id>", methods=["POST"])
def mb52_satir_sira_guncelle(satir_id):
    """
    Bir satırın sıra numarasını güncelle.
    Body: {sira_no: 8}  (boş bırakırsa null'a çevirir)
    """
    d = request.get_json(silent=True) or {}
    yeni_sira = d.get("sira_no")

    # Boş / null gönderildiyse null yap
    if yeni_sira is None or yeni_sira == "":
        yeni_sira_val = None
    else:
        try:
            yeni_sira_val = int(yeni_sira)
        except (TypeError, ValueError):
            return jsonify({"durum": "hata", "mesaj": "Sıra numarası sayı olmalı"}), 400

    conn = get_conn()
    cur = conn.cursor()
    aktif_tablo = _aktif_tablo_bul(conn)
    if not aktif_tablo:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "Tablo yok"}), 500

    mevcut_kolonlar = _tablo_kolonlari(conn, aktif_tablo)
    if "sira_no" not in mevcut_kolonlar:
        conn.close()
        return jsonify({"durum": "hata", "mesaj": "sira_no kolonu yok"}), 500

    cur.execute(f"UPDATE {aktif_tablo} SET sira_no = ? WHERE id = ?", (yeni_sira_val, satir_id))
    etkilenen = cur.rowcount
    conn.commit()
    conn.close()

    if etkilenen == 0:
        return jsonify({"durum": "hata", "mesaj": "Satır bulunamadı"}), 404

    return jsonify({
        "durum": "ok",
        "satir_id": satir_id,
        "sira_no": yeni_sira_val
    })


# ───────────────────────────────────────────────────────────────────────────
# MAL GRUBU MANUEL DURUM — kullanıcı override
# ───────────────────────────────────────────────────────────────────────────

@mb52_bp.route("/api/mb52/mal-grubu-durum/<path:mg_kodu>", methods=["POST"])
def mb52_mal_grubu_durum_guncelle(mg_kodu):
    """
    Bir mal grubunun sayım durumunu manuel olarak ayarla.
    Body: {durum: "tamamlandi"|"devam_ediyor"|"sayilmadi"|"auto"}
    "auto" gönderirse manuel override kaldırılır, otomatik hesaba döner.
    """
    d = request.get_json(silent=True) or {}
    durum = (d.get("durum") or "").strip().lower()
    gecerli = {"sayilmadi", "devam_ediyor", "tamamlandi", "auto"}
    if durum not in gecerli:
        return jsonify({"durum": "hata", "mesaj": f"Geçersiz durum: {durum}"}), 400

    conn = get_conn()
    cur = conn.cursor()
    try:
        if durum == "auto":
            # Manuel kaydı sil — otomatik hesaba dön
            cur.execute("DELETE FROM mal_grubu_durum WHERE mal_grubu_kodu = ?", (mg_kodu,))
            mesaj = f"{mg_kodu}: otomatik moda alındı"
        else:
            # Upsert — varsa güncelle, yoksa ekle
            cur.execute("""
                INSERT INTO mal_grubu_durum (mal_grubu_kodu, durum, guncelleme_tarihi)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(mal_grubu_kodu) DO UPDATE SET
                    durum             = excluded.durum,
                    guncelleme_tarihi = CURRENT_TIMESTAMP
            """, (mg_kodu, durum))
            mesaj = f"{mg_kodu}: {durum} olarak işaretlendi"
        conn.commit()
        return jsonify({
            "durum": "ok",
            "mesaj": mesaj,
            "mal_grubu_kodu": mg_kodu,
            "yeni_durum": durum if durum != "auto" else None,
            "manuel": durum != "auto"
        })
    except Exception as e:
        try: conn.rollback()
        except: pass
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        try: conn.close()
        except: pass