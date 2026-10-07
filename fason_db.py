# -*- coding: utf-8 -*-
"""
fason_db.py — BBA Fason İrsaliye Takip Modülü
Firma yönetimi + İşlem Geçmişi entegrasyonu dahil versiyon.
"""

import os
import sys
import sqlite3
from datetime import datetime
from flask import Blueprint, request, jsonify, session
from kullanici_db import VARSAYILAN_ROLLER
from difflib import SequenceMatcher
from openpyxl.worksheet.datavalidation import DataValidation

def _benzerlik_orani(a, b):
    a = (a or "").strip().upper()
    b = (b or "").strip().upper()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def _simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _fason_yetki_var_mi(yetki):
    rol = session.get("rol")
    return VARSAYILAN_ROLLER.get(rol, {}).get(yetki, False)

fason_bp = Blueprint("fason", __name__)

ORTAK_KLASOR = r"K:\Warehouse\Yeşilovacık\12_Paylaşım Klasörü\01-BBA\bba-tool"


def _db_klasor_bul():
    # fason.db artık HER ZAMAN yerel diskte yaşıyor (K: ağ gecikmesi sorununu çözmek için).
    # K: sürücüsü sadece "senkronize et" butonuyla manuel yedekleme/paylaşım için kullanılıyor.
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DB_KLASOR = _db_klasor_bul()
DB_YOL = os.path.join(DB_KLASOR, "fason.db")

# ═════════════════════════════════════════════════
# İŞLEM GEÇMİŞİ ENTEGRASYONU (sevkiyat.db'deki ortak islem_log tablosu)
# ═════════════════════════════════════════════════
SEVKIYAT_DB_YOL = os.path.join(DB_KLASOR, "sevkiyat.db")


def log_kaydet(islem, detay="", ilgili_id=None, ilgili_ad=""):
    """Fason işlemlerini merkezi İşlem Geçmişi tablosuna (sevkiyat.db → islem_log) yazar."""
    try:
        conn = sqlite3.connect(SEVKIYAT_DB_YOL, timeout=30, check_same_thread=False)
        conn.execute(
            "INSERT INTO islem_log (modul,islem,detay,ilgili_id,ilgili_ad,yapan,yapan_ad,tarih) VALUES (?,?,?,?,?,?,?,?)",
            ("Fason", islem, detay, ilgili_id, ilgili_ad,
             session.get("kullanici", "sistem"), session.get("ad", "Sistem"),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Fason log] hata: {e}")


def _fason_export_klasor():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _connect_db(db_yol):
    os.makedirs(os.path.dirname(db_yol), exist_ok=True)
    conn = sqlite3.connect(db_yol, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # NOT: Ölçümler gösterdi ki WAL, ağ sürücüsünde (K:) HER bağlantıda ölçülebilir bir
    # ek maliyet getiriyor (bkz. 2026-09 açılış performansı incelemesi — WAL: ~9.7sn,
    # DELETE: ~3sn toplam açılış). Bu sadece açılışta değil, gün boyu her istek K:'ye
    # bağlandığında tekrarlanan bir maliyet olduğu için DELETE modunda kalıyoruz.
    # Risk: DELETE modu yazma sırasında dosyayı kilitler (WAL'daki gibi eşzamanlı
    # okuma/yazma yok) — çoklu kullanıcı aynı anda yazarsa "database is locked"
    # ihtimali artar. busy_timeout bunu 5 saniyeye kadar tolere ediyor.
    ag_yolu = os.path.isdir(ORTAK_KLASOR) and os.path.normcase(os.path.abspath(db_yol)).startswith(os.path.normcase(os.path.abspath(ORTAK_KLASOR)))
    if ag_yolu:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _aktif_db_yolu():
    # admin rolü hızlı yerel veritabanını kullanır (senkronizasyon onun elinde).
    # Diğer herkes doğrudan K:'deki (admin'in yedeklediği) veritabanını okur/yazar.
    if session.get("rol") == "admin":
        return DB_YOL
    if os.path.isdir(ORTAK_KLASOR):
        return os.path.join(ORTAK_KLASOR, "fason.db")
    # K: şu an erişilemiyorsa (ağ koptu vb.) çökmemek için yerel dosyaya düş
    return DB_YOL


def get_db():
    return _connect_db(_aktif_db_yolu())

def _kolon_var_mi(conn, tablo, kolon):
    r = conn.execute(f"PRAGMA table_info({tablo})").fetchall()
    return any(row[1] == kolon for row in r)


def _mevcut_kolonlar(conn, tablo):
    """Bir tablonun tüm kolon adlarını TEK sorguda döner (ağ üzerinde her kolon için
    ayrı PRAGMA çağrısı yapmaktan kaçınmak için — bkz. init süresi optimizasyonu)."""
    r = conn.execute(f"PRAGMA table_info({tablo})").fetchall()
    return {row[1] for row in r}


def _migrate_ek_kolonlar(conn, yerel=True):
    import time as _t
    _b = _t.time()
    yeni_kolonlar = [
        ("irsaliye_tarihi", "TEXT"),
        ("stok_miktari",    "REAL"),
        ("giris_miktari",   "REAL"),
        ("otis_stok",       "REAL"),
        ("otis_giris",      "REAL"),
        ("toplam_fiyat",    "REAL"),
        ("firma_id",        "INTEGER"),
    ]
    mevcut = _mevcut_kolonlar(conn, "fason_irsaliye")
    print(f"[FASON-TANI]   mevcut_kolonlar(fason_irsaliye) tamam: {_t.time()-_b:.2f} sn", flush=True)
    eksikler = [(ad, tip) for ad, tip in yeni_kolonlar if ad not in mevcut]
    if eksikler:
        conn.executescript(";\n".join(
            f"ALTER TABLE fason_irsaliye ADD COLUMN {ad} {tip}" for ad, tip in eksikler
        ) + ";")
        for ad, _ in eksikler:
            print(f"[Fason DB] Kolon eklendi: {ad}")
    print(f"[FASON-TANI]   irsaliye alter (varsa) tamam: {_t.time()-_b:.2f} sn", flush=True)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS fason_kalem (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            irsaliye_id         INTEGER NOT NULL,
            malzeme_tanim       TEXT NOT NULL DEFAULT '',
            sap_kodu            TEXT DEFAULT '',
            irsaliye_tarihi     TEXT,
            stok_miktari        REAL,
            giris_miktari       REAL,
            otis_stok           REAL,
            otis_giris          REAL,
            toplam_fiyat        REAL,
            para_birimi         TEXT DEFAULT '',
            birim_fiyat         REAL,
            belge_tarihi        TEXT,
            fark_sebebi         TEXT DEFAULT '',
            olusturma_tarihi    TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (irsaliye_id) REFERENCES fason_irsaliye(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_kalem_irsaliye ON fason_kalem(irsaliye_id);
        CREATE INDEX IF NOT EXISTS idx_kalem_sap_kodu ON fason_kalem(sap_kodu);
        CREATE INDEX IF NOT EXISTS idx_kalem_tanim ON fason_kalem(malzeme_tanim);
    """)
    print(f"[FASON-TANI]   fason_kalem create+index tamam: {_t.time()-_b:.2f} sn", flush=True)

    # OTIS eşleştirme kimliği + stok değişim takibi — fason_kalem oluşturulduktan SONRA eklenmeli
    mevcut_kalem = _mevcut_kolonlar(conn, "fason_kalem")
    ek_kolonlar = [
        ("otis_malzeme_tanim", "TEXT"), ("otis_sap_kodu", "TEXT"),
        ("durum", "TEXT DEFAULT 'Stok'"), ("cikis_irsaliye_no", "TEXT DEFAULT ''"),
        # Rapor ekranındaki giriş/çıkış trendi için: 'durum' her değiştiğinde bu alan da
        # güncellenir (bkz. api_fason_kalem_guncelle). Bu kolon eklenmeden ÖNCE zaten
        # "Çıkış"/"Çıkış (A63)" olan kayıtlarda boş kalır — geçmişe dönük tarih bilinmiyor,
        # o kayıtlar tekrar güncellenene kadar trend grafiğine dahil edilmez.
        ("durum_tarihi", "TEXT"),
        # OTIS eşleşmesinin nasıl yapıldığı — "OTIS Eşleşmeleri" kontrol ekranı için.
        # 'sap' (SAP kodu ile otomatik), 'benzerlik' (%98+ metin), 'elle', 'yeni_kalem' (OTIS'ten açılan kalem).
        # Bu kolonlar eklenmeden önce yapılmış eşleşmelerde boş kalır ("eski kayıt").
        ("otis_eslesme_tipi", "TEXT"), ("otis_eslesme_tarihi", "TEXT"), ("otis_eslesen_kullanici", "TEXT"),
    ]
    eksik_kalem = [(ad, tip) for ad, tip in ek_kolonlar if ad not in mevcut_kalem]
    if eksik_kalem:
        conn.executescript(";\n".join(
            f"ALTER TABLE fason_kalem ADD COLUMN {ad} {tip}" for ad, tip in eksik_kalem
        ) + ";")
    # 'durum' kolonunda index yoksa aşağıdaki UPDATE (ve durum'a göre filtreleyen her sorgu)
    # tüm tabloyu tarar — ağ sürücüsünde bu saniyeler sürebilir. Index'i garantiye al.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kalem_durum ON fason_kalem(durum)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kalem_durum_tarihi ON fason_kalem(durum_tarihi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kalem_irsaliye_tarihi ON fason_kalem(irsaliye_tarihi)")
    print(f"[FASON-TANI]   kalem alter + durum index (varsa) tamam: {_t.time()-_b:.2f} sn", flush=True)
    # Stok miktarı hiç girilmemiş kalemlerde "Stok" durumu otomatik atanmış olabilir — temizle.
    # Bu tek seferlik bir veri temizliği; 'durum' kolonu varsayılan olarak 'Stok' geldiği için
    # index bu sorguyu ayırt edemiyor (neredeyse tüm satırlar eşleşiyor) — ağdaki büyüyen
    # ortak tabloya karşı her açılışta taratmanın maliyeti çok yüksek. Sadece yerelde çalıştır.
    # NOT: Eskiden burada her açılışta "UPDATE ... SET durum = '' WHERE durum = 'Stok' AND stok_miktari IS NULL"
    # çalışıyordu — elle "Stok" verilen (stok miktarı girilmemiş) kalemleri her açılışta Belirlenmedi'ye
    # döndürüyordu. Artık tüm kalem eklemeleri durumu açıkça yazdığı için (ZMM068 / OTIS / elle) kaldırıldı.

    _fason_kalem_migration(conn)
    print(f"[FASON-TANI]   _fason_kalem_migration tamam: {_t.time()-_b:.2f} sn", flush=True)


def _irsaliye_tarihi_doldur(conn, irs_idler=None):
    """İrsaliye tarihi BOŞ olan irsaliyelere ZMM068 belge tarihini (kalemlerinin en eskisi, GG.AA.YYYY) yazar.
    Dolu tarihlere dokunmaz. Döner: güncellenen irsaliye sayısı."""
    kosul = ""
    params = []
    if irs_idler:
        irs_idler = list(irs_idler)
        kosul = f" AND fason_irsaliye.id IN ({','.join('?' * len(irs_idler))})"
        params = irs_idler
    cur = conn.execute(f"""
        UPDATE fason_irsaliye SET irsaliye_tarihi = (
            SELECT substr(MIN(NULLIF(k.belge_tarihi, '')), 9, 2) || '.' || substr(MIN(NULLIF(k.belge_tarihi, '')), 6, 2)
                   || '.' || substr(MIN(NULLIF(k.belge_tarihi, '')), 1, 4)
            FROM fason_kalem k WHERE k.irsaliye_id = fason_irsaliye.id
        )
        WHERE COALESCE(fason_irsaliye.irsaliye_tarihi, '') = ''
          AND EXISTS (SELECT 1 FROM fason_kalem k WHERE k.irsaliye_id = fason_irsaliye.id AND COALESCE(k.belge_tarihi, '') <> ''){kosul}
    """, params)
    return cur.rowcount or 0


def _cikis_no_listesi(s):
    """'A, B; C' / 'A B' gibi bir metinden tekrar etmeyen irsaliye no listesi çıkarır."""
    import re as _re
    sonuc = []
    for p in _re.split(r"[,;/\s]+", str(s or "")):
        p = p.strip()
        if p and p not in sonuc:
            sonuc.append(p)
    return sonuc


def _tr_sayi(s):
    """'1.234,56' / '116,46' / '116.46' / 116.46 → float. Boş → None. Hatalıysa ValueError."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _miktar_listesi(v):
    """Excel 'Çıkış Miktarı' hücresi → [miktar, ...]. Birden fazla miktar ';' ile ayrılır
    (virgül Türkçe ondalık ayırıcı olduğu için ayraç olarak kullanılmaz). Boş eleman → None."""
    import re as _re
    if v is None or (isinstance(v, str) and not v.strip()):
        return []
    if isinstance(v, (int, float)):
        return [float(v)]
    sonuc = []
    for p in _re.split(r"[;|\n]", str(v)):
        try:
            sonuc.append(_tr_sayi(p))
        except ValueError:
            sonuc.append(None)
    return sonuc


def _tr_sayi_yaz(m):
    if m is None:
        return ""
    return (f"{m:.3f}".rstrip("0").rstrip(".") or "0").replace(".", ",")


def _cikis_miktar_hucresi(no_miktar):
    """{irsaliye_no: miktar} (sıralı) → Excel hücresi: tek miktar sayı, birden fazlaysa '116,46; 38,82'."""
    miktarlar = list(no_miktar.values())
    if not miktarlar:
        return None
    if len(miktarlar) == 1:
        return miktarlar[0]
    return "; ".join(_tr_sayi_yaz(m) for m in miktarlar)


def _tarih_iso(v):
    """Excel hücresi / metin → 'YYYY-AA-GG'. Boş → None. Anlaşılamazsa ValueError."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):   # Excel seri tarih numarası (biçimsiz hücre)
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d")
    t = str(v).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Tarih anlaşılamadı: {t}")


def _tarih_listesi(v):
    """Excel 'Çıkış Tarihi' hücresi → [iso|None, ...] (';' ile ayrılmış, irsaliyelerle aynı sıra).
    Anlaşılamayan eleman 'HATA:<metin>' olarak döner."""
    import re as _re
    if v is None or (isinstance(v, str) and not v.strip()):
        return []
    if not isinstance(v, str):
        try:
            return [_tarih_iso(v)]
        except ValueError:
            return ["HATA:" + str(v)]
    sonuc = []
    for p in _re.split(r"[;|\n]", v):
        try:
            sonuc.append(_tarih_iso(p))
        except ValueError:
            sonuc.append("HATA:" + p.strip())
    return sonuc


def _cikis_tarih_hucresi(no_tarih):
    """{irsaliye_no: 'YYYY-AA-GG'} → Excel hücresi: tek tarih gerçek tarih hücresi, birden fazlaysa '05.09.2026; 21.09.2026'."""
    tarihler = list(no_tarih.values())
    if not tarihler:
        return None
    if len(tarihler) == 1:
        try:
            return datetime.strptime(tarihler[0][:10], "%Y-%m-%d") if tarihler[0] else None
        except ValueError:
            return tarihler[0]
    def tr(t):
        try:
            return datetime.strptime(t[:10], "%Y-%m-%d").strftime("%d.%m.%Y") if t else ""
        except ValueError:
            return t or ""
    return "; ".join(tr(t) for t in tarihler)


# ─── Kalem hareketleri (fason_kalem_cikis) ───
# Bir malzeme önce tadilata/boyaya ya da Akkuyu'ya (nakil) gidebilir, sonra satılabilir/çıkabilir.
# Sadece Çıkış ve Çıkış (A63) KESİN çıkıştır. Tadilat gönderimi ve Nakil kalanı düşürmez —
# "tadilatta" / "nakilde" olarak ayrı izlenir.
HAREKET_TIPLERI = {
    "cikis": "Satış / Çıkış",
    "a63": "Çıkış (A63)",
    "nakil": "Nakil / Akkuyu",
    "tadilat_gidis": "Tadilat / Boya gönderimi",
    "tadilat_donus": "Tadilattan dönüş",
}
KESIN_CIKIS_TIPLERI = ("cikis", "a63")
# Kalem durumu ↔ hareket tipi
DURUM_HAREKET_TIPI = {"Çıkış": "cikis", "Çıkış (A63)": "a63", "Nakil": "nakil", "Tadilat": "tadilat_gidis", "Aspro": "cikis"}
HAREKET_TIPI_DURUM = {"cikis": "Çıkış", "a63": "Çıkış (A63)"}


def _cikis_ozet_guncelle(conn, kalem_id):
    """fason_kalem.cikis_irsaliye_no özet alanını hareket tablosundan yeniden yazar
    (dönüşler hariç; numarası olan tüm gidiş/çıkış irsaliyeleri, tarih sırasıyla)."""
    nolar = []
    for r in conn.execute("""
        SELECT irsaliye_no FROM fason_kalem_cikis
        WHERE kalem_id = ? AND COALESCE(tip, 'cikis') <> 'tadilat_donus' AND irsaliye_no <> ''
        ORDER BY COALESCE(tarih, ''), id
    """, (kalem_id,)).fetchall():
        if r[0] not in nolar:
            nolar.append(r[0])
    conn.execute("UPDATE fason_kalem SET cikis_irsaliye_no = ? WHERE id = ?", (", ".join(nolar), kalem_id))


def _hareket_hesapla(giris, kesin, gidis, donus, nakil, eksik):
    """Hareket toplamlarından depoda / tadilatta / nakilde / kalan hesabı.
    Tadilattan dönüş girilmemiş olabilir; nakilden sonra Akkuyu'dan doğrudan satış girilebilir.
    Kesin çıkış depodaki miktarı aşıyorsa, aşan kısım önce tadilattan (geri gelmiş sayılır),
    sonra nakilden düşülür."""
    sonuc = {"giris": giris, "kesin_cikan": round(kesin, 3), "tadilat_gidis": round(gidis, 3),
             "tadilat_donus": round(donus, 3), "nakil_toplam": round(nakil, 3),
             "tadilatta": None, "nakilde": None, "depoda": None, "kalan": None,
             "miktari_eksik": bool(eksik)}
    sonuc["toplam_cikan"] = sonuc["kesin_cikan"]   # eski alan adı (ekranlar için)
    if giris is None or eksik:
        return sonuc
    tadilatta = max(0.0, gidis - donus)
    nakilde = nakil
    depoda = giris - tadilatta - nakilde - kesin
    if depoda < 0:
        acik = -depoda
        dus = min(acik, tadilatta); tadilatta -= dus; acik -= dus
        dus = min(acik, nakilde); nakilde -= dus; acik -= dus
        depoda = -acik            # hâlâ eksiyse: girişten fazla kesin çıkış var
    r3 = lambda x: round(x, 3) + 0.0   # -0.0 ekranda "-0" görünmesin
    sonuc.update({"tadilatta": r3(tadilatta), "nakilde": r3(nakilde),
                  "depoda": r3(depoda), "kalan": r3(giris - kesin)})
    return sonuc


def _cikis_ozet_hesapla(conn, kalem_id):
    """Kalemin giriş / kesin çıkan / tadilatta / nakilde / depoda / kalan bilgisi.
    Miktarı girilmemiş hareket varsa miktar hesapları yapılmaz."""
    k = conn.execute("SELECT giris_miktari FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
    satirlar = conn.execute("SELECT COALESCE(tip, 'cikis'), miktar FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,)).fetchall()
    top = lambda tipler: sum((m or 0) for t, m in satirlar if t in tipler)
    oz = _hareket_hesapla(k[0] if k else None, top(KESIN_CIKIS_TIPLERI), top(("tadilat_gidis",)),
                          top(("tadilat_donus",)), top(("nakil",)), any(m is None for t, m in satirlar))
    oz["cikis_sayisi"] = len(satirlar)
    return oz


def _cikis_ekle(conn, kalem_id, irsaliye_no, miktar=None, tarih=None, kaynak="elle", kullanici="", tip="cikis"):
    """Kaleme hareket (çıkış / tadilat gidiş-dönüş) ekler. Aynı irsaliye no zaten varsa yeni kayıt
    açmaz; miktar verildiyse onu günceller. Dönüş hareketinde irsaliye no boş olabilir.
    Döner: 'eklendi' / 'guncellendi' / 'vardi' / None"""
    irsaliye_no = (irsaliye_no or "").strip()
    tip = tip if tip in HAREKET_TIPLERI else "cikis"
    if not irsaliye_no and tip != "tadilat_donus":
        return None
    if irsaliye_no:
        var = conn.execute("SELECT id, miktar FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?",
                           (kalem_id, irsaliye_no)).fetchone()
        if var:
            if miktar is not None and var[1] != miktar:
                conn.execute("UPDATE fason_kalem_cikis SET miktar = ? WHERE id = ?", (miktar, var[0]))
                return "guncellendi"
            return "vardi"
    conn.execute("""
        INSERT INTO fason_kalem_cikis (kalem_id, irsaliye_no, miktar, tarih, kaynak, ekleyen, tip)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (kalem_id, irsaliye_no, miktar, tarih or datetime.now().strftime("%Y-%m-%d"), kaynak, kullanici, tip))
    return "eklendi"


def _cikis_aktarim(conn):
    """Eski tek alanlı cikis_irsaliye_no değerlerini hareket tablosuna taşır (bir kez; zaten
    aktarılmış kalemlere dokunmaz). Miktar tahmini: giriş - mevcut stok (stok boşsa tamamı çıkmış sayılır).
    Tip kalemin durumundan çıkarılır (Tadilat → tadilat gönderimi, Nakil → nakil, A63 → a63)."""
    # 'tip' kolonu sonradan eklendi — bu sürümden önce aktarılmış kayıtlar varsa onların tipini de düzelt
    kolonlar = {r[1] for r in conn.execute("PRAGMA table_info(fason_kalem_cikis)").fetchall()}
    if "tip" not in kolonlar:
        conn.execute("ALTER TABLE fason_kalem_cikis ADD COLUMN tip TEXT DEFAULT 'cikis'")
        for durum, tip in DURUM_HAREKET_TIPI.items():
            if tip != "cikis":
                conn.execute("""
                    UPDATE fason_kalem_cikis SET tip = ?
                    WHERE kaynak = 'aktarim' AND kalem_id IN (SELECT id FROM fason_kalem WHERE durum = ?)
                """, (tip, durum))
        conn.commit()

    rows = conn.execute("""
        SELECT k.id, k.cikis_irsaliye_no, k.giris_miktari, k.stok_miktari, k.durum_tarihi, k.durum
        FROM fason_kalem k
        WHERE COALESCE(k.cikis_irsaliye_no, '') <> ''
          AND NOT EXISTS (SELECT 1 FROM fason_kalem_cikis c WHERE c.kalem_id = k.id)
    """).fetchall()
    if not rows:
        return
    for r in rows:
        nolar = _cikis_no_listesi(r[1])
        miktar = None
        if len(nolar) == 1 and r[2] is not None:
            m = r[2] - (r[3] or 0)
            miktar = round(m, 3) if m > 0.005 else None
        tarih = (r[4] or "")[:10] or None
        tip = DURUM_HAREKET_TIPI.get(r[5] or "", "cikis")
        for no in nolar:
            conn.execute("""
                INSERT INTO fason_kalem_cikis (kalem_id, irsaliye_no, miktar, tarih, kaynak, ekleyen, tip)
                VALUES (?, ?, ?, ?, 'aktarim', '', ?)
            """, (r[0], no, miktar, tarih, tip))
    conn.commit()
    print(f"[Fason DB] {len(rows)} kalemin çıkış irsaliyesi yeni hareket tablosuna aktarıldı", flush=True)


def _hareket_sonrasi_durum(conn, kalem_id, son_tip):
    """Hareket eklendikten sonra kalemin durumunu mantıklı hale getirir (sadece Stok / boş / Tadilat
    durumundaki kalemlerde — elle verilmiş diğer durumlara dokunmaz). Döner: yeni durum ya da None."""
    k = conn.execute("SELECT durum FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
    mevcut = (k[0] or "") if k else None
    if mevcut not in ("", "Stok", "Tadilat", "Nakil"):
        return None
    oz = _cikis_ozet_hesapla(conn, kalem_id)
    if oz["kalan"] is None:
        return None
    yeni = None
    if oz["kalan"] <= 0.005 and son_tip in HAREKET_TIPI_DURUM:
        yeni = HAREKET_TIPI_DURUM[son_tip]                    # malzemenin tamamı kesin çıktı
    elif oz["depoda"] <= 0.005 and oz["tadilatta"] > 0.005 and oz["nakilde"] <= 0.005:
        yeni = "Tadilat"                                     # kalan malzemenin tamamı tadilatta
    elif oz["depoda"] <= 0.005 and oz["nakilde"] > 0.005 and oz["tadilatta"] <= 0.005:
        yeni = "Nakil"                                       # kalan malzemenin tamamı Akkuyu'da
    elif mevcut in ("Tadilat", "Nakil") and oz["depoda"] > 0.005 and oz["tadilatta"] <= 0.005 and oz["nakilde"] <= 0.005:
        yeni = "Stok"                                        # geri döndü, depoda bekliyor
    if yeni and yeni != (k[0] or ""):
        conn.execute("UPDATE fason_kalem SET durum = ?, durum_tarihi = datetime('now','localtime') WHERE id = ?", (yeni, kalem_id))
        return yeni
    return None


def _fason_kalem_migration(conn):
    """Mevcut tek-satır irsaliyeleri, geriye dönük uyumluluk için tek bir kaleme dönüştürür.
    Yalnızca fason_kalem tablosu tamamen boşsa çalışır (tekrar tekrar migrate etmez)."""
    kalem_var_mi = conn.execute("SELECT COUNT(*) FROM fason_kalem").fetchone()[0]
    if kalem_var_mi > 0:
        return

    irsaliyeler = conn.execute("""
        SELECT id, aciklama, irsaliye_tarihi, stok_miktari, giris_miktari,
               otis_stok, otis_giris, toplam_fiyat
        FROM fason_irsaliye
    """).fetchall()
    print(f"[FASON-TANI]     migration: {len(irsaliyeler)} satır bulundu, INSERT döngüsü başlıyor", flush=True)

    veriler = [
        ((irs["aciklama"] or "").strip() or "Genel Kalem", irs["id"], irs["irsaliye_tarihi"],
         irs["stok_miktari"], irs["giris_miktari"], irs["otis_stok"], irs["otis_giris"], irs["toplam_fiyat"])
        for irs in irsaliyeler
    ]
    conn.executemany("""
        INSERT INTO fason_kalem
            (malzeme_tanim, irsaliye_id, irsaliye_tarihi, stok_miktari, giris_miktari,
             otis_stok, otis_giris, toplam_fiyat)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, veriler)

    if irsaliyeler:
        print(f"[Fason DB] Migration: {len(irsaliyeler)} irsaliye tek kaleme dönüştürüldü (malzeme adı: açıklama varsa o, yoksa 'Genel Kalem')")


# Şema / tek seferlik veri düzeltmelerinin sürümü — DB dosyasının içinde (PRAGMA user_version) tutulur.
# Damga bu değere eşitse açılışta tablo/kolon kontrolleri ve veri düzeltmeleri ATLANIR (K: ağ sürücüsünde
# bunlar her açılışta ~30 sn sürüyordu). Yeni tablo/kolon ya da tek seferlik düzeltme eklersen bu sayıyı 1 artır.
_FASON_SEMA_SURUMU = 5   # 2: OTIS çıkış → SAP ölçeği · 3: fason_otis_cikis_satir · 4: sipariş no + ayarlar · 5: alıcı (TESLİM ALAN)


def _init_fason_db_at(db_yol):
    import time as _t
    _b = _t.time()
    def _tan(etiket):
        print(f"[FASON-TANI] {etiket} ({db_yol}): {_t.time()-_b:.2f} sn", flush=True)
    conn = _connect_db(db_yol)
    _tan("_connect_db tamam")
    surum = 0
    try:
        surum = conn.execute("PRAGMA user_version").fetchone()[0]
        if surum >= _FASON_SEMA_SURUMU:
            _tan(f"şema güncel (sürüm {surum}) — kontroller atlandı")
            conn.close()
            return
    except Exception as e:
        print(f"[Fason DB] Şema sürümü okunamadı, tam kontrol yapılacak: {e}", flush=True)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fason_durum (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad TEXT NOT NULL UNIQUE,
                renk TEXT DEFAULT 'text3',
                ikon TEXT DEFAULT 'fa-circle',
                sira INTEGER DEFAULT 0,
                aktif INTEGER DEFAULT 1,
                olusturulma TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS fason_firma (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ad TEXT NOT NULL UNIQUE,
                aktif INTEGER DEFAULT 1,
                olusturulma TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS fason_irsaliye (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                irsaliye_no TEXT NOT NULL,
                aciklama TEXT DEFAULT '',
                giren_kullanici TEXT NOT NULL,
                girilme_tarihi TEXT DEFAULT (datetime('now', 'localtime')),
                durum_id INTEGER,
                durum_notu TEXT DEFAULT '',
                durum_guncelleyen TEXT DEFAULT '',
                durum_guncelleme_tarihi TEXT DEFAULT '',
                FOREIGN KEY (durum_id) REFERENCES fason_durum(id)
            );

            CREATE TABLE IF NOT EXISTS fason_otis_bekleyen (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                irsaliye_id         INTEGER NOT NULL,
                malzeme_tanim       TEXT NOT NULL,
                mal_grubu           TEXT DEFAULT '',
                sap_kodu            TEXT DEFAULT '',
                otis_stok           REAL,
                otis_giris          REAL,
                olusturma_tarihi    TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (irsaliye_id) REFERENCES fason_irsaliye(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_bekleyen_irsaliye ON fason_otis_bekleyen(irsaliye_id);

            -- Bir kalem birden fazla çıkış irsaliyesiyle (parça parça) çıkabilir.
            -- fason_kalem.cikis_irsaliye_no bu tablonun özeti olarak tutulur ("A, B") —
            -- arama/filtre/Excel gibi eski yerler çalışmaya devam etsin diye.
            CREATE TABLE IF NOT EXISTS fason_kalem_cikis (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                kalem_id     INTEGER NOT NULL,
                irsaliye_no  TEXT NOT NULL,
                miktar       REAL,
                tarih        TEXT,
                kaynak       TEXT DEFAULT 'elle',   -- elle / toplu / mb52 / aktarim (eski tek alandan)
                ekleyen      TEXT DEFAULT '',
                tip          TEXT DEFAULT 'cikis',  -- cikis / a63 / nakil / tadilat_gidis / tadilat_donus
                olusturma    TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (kalem_id) REFERENCES fason_kalem(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_cikis_kalem ON fason_kalem_cikis(kalem_id);
            CREATE INDEX IF NOT EXISTS idx_cikis_irsaliye ON fason_kalem_cikis(irsaliye_no);

            -- OTIS çıkış export'u: GÖNDERİM YERİ → hareket tipi eşlemesi (bir kez seçilir, hatırlanır)
            CREATE TABLE IF NOT EXISTS fason_gonderim_yeri (
                yer          TEXT PRIMARY KEY,
                tip          TEXT NOT NULL,          -- cikis / a63 / nakil / tadilat_gidis / yoksay
                guncelleyen  TEXT DEFAULT '',
                guncelleme   TEXT DEFAULT (datetime('now', 'localtime'))
            );

            -- OTIS çıkış export'unda giriş irsaliyesi (AÇIKLAMA-2) bizde olmayan satırlar — sonra tekrar denenir
            CREATE TABLE IF NOT EXISTS fason_otis_cikis_bekleyen (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                cikis_no       TEXT NOT NULL,
                tarih          TEXT,
                yer            TEXT DEFAULT '',
                giris_no       TEXT DEFAULT '',
                sap_kodu       TEXT DEFAULT '',
                malzeme_tanim  TEXT DEFAULT '',
                miktar         REAL,
                kantar         REAL,
                satir_sayisi   INTEGER DEFAULT 1,
                olusturma      TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE (cikis_no, giris_no, sap_kodu, malzeme_tanim)
            );

            -- SAP çıkış hareketleri (çıkış ZMM068'i) — OTIS / sistem hareketleriyle karşılaştırmak için.
            -- miktar: çıkış pozitif, ters kayıt (storno) negatif → (SAP kodu, çıkış no) toplamı net çıkış.
            CREATE TABLE IF NOT EXISTS fason_sap_cikis (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                malzeme_belgesi TEXT NOT NULL,
                yil             TEXT DEFAULT '',
                belge_kalem     TEXT DEFAULT '',
                cikis_no        TEXT DEFAULT '',
                sap_kodu        TEXT DEFAULT '',
                malzeme_tanim   TEXT DEFAULT '',
                miktar          REAL,
                belge_tarihi    TEXT,
                islem_turu      TEXT DEFAULT '',
                depo            TEXT DEFAULT '',
                baslik          TEXT DEFAULT '',
                yukleme         TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE (malzeme_belgesi, yil, belge_kalem)
            );
            CREATE INDEX IF NOT EXISTS idx_sap_cikis_anahtar ON fason_sap_cikis(sap_kodu, cikis_no);
            CREATE INDEX IF NOT EXISTS idx_sap_cikis_no ON fason_sap_cikis(cikis_no);

            -- OTIS çıkış export'unun ham satırları (giriş irsaliyesi bazında, eşleşsin eşleşmesin hepsi).
            -- SAP çıkışı giriş irsaliyesi bilmez; bir çıkışın SAP'deki toplamı, OTIS'te o çıkışın TÜM girişlerdeki
            -- toplamıyla karşılaştırılır ve her girişin payı buradan bulunur.
            CREATE TABLE IF NOT EXISTS fason_otis_cikis_satir (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                cikis_no   TEXT NOT NULL,
                giris_no   TEXT DEFAULT '',
                sap_kodu   TEXT DEFAULT '',
                tanim      TEXT DEFAULT '',
                kg         REAL,
                tarih      TEXT,
                yer        TEXT DEFAULT '',
                siparis    TEXT DEFAULT '',    -- OTIS ÇIKIŞ SİPARİŞ NO (SAP çıkış Excel'inde "Metin")
                alici      TEXT DEFAULT ''     -- OTIS TESLİM ALAN (SAP çıkış Excel'inde "Alıcı")
            );
            CREATE INDEX IF NOT EXISTS idx_ocs_no ON fason_otis_cikis_satir(cikis_no);

            -- SAP çıkış Excel'i (41 / 47 formatı) için alıcı firmaya göre sabitler (OTIS gönderim yeri → sipariş / müşteri)
            CREATE TABLE IF NOT EXISTS fason_sap_cikis_ayar (
                yer      TEXT PRIMARY KEY,     -- OTIS gönderim yeri (ör. ICN, TSM ENERJİ)
                siparis  TEXT DEFAULT '',
                musteri  TEXT DEFAULT '',
                alici    TEXT DEFAULT '',
                firma    TEXT DEFAULT ''       -- Excel'deki "Çıkış Firma" (tek tip)
            );

            -- Her SAP çıkış yüklemesinin kapsadığı tarih aralığı. "SAP'de yapılmamış" sadece
            -- bu aralıklara düşen hareketler için söylenir (parça parça yüklemede yanlış alarm olmasın).
            CREATE TABLE IF NOT EXISTS fason_sap_yukleme (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                dosya     TEXT DEFAULT '',
                bas       TEXT NOT NULL,
                bit       TEXT NOT NULL,
                satir     INTEGER DEFAULT 0,
                yukleyen  TEXT DEFAULT '',
                tarih     TEXT DEFAULT (datetime('now', 'localtime'))
            );
        """)
        _tan("1. executescript (4 tablo+1 index) tamam")

        _migrate_ek_kolonlar(conn, yerel=(db_yol == DB_YOL))
        _tan("_migrate_ek_kolonlar tamam")

        _cikis_aktarim(conn)
        _tan("_cikis_aktarim tamam")

        doldurulan = _irsaliye_tarihi_doldur(conn)
        if doldurulan:
            conn.commit()
            print(f"[Fason DB] {doldurulan} irsaliyenin boş tarihi ZMM068 belge tarihinden dolduruldu", flush=True)

        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_irsaliye_no ON fason_irsaliye(irsaliye_no);
            CREATE INDEX IF NOT EXISTS idx_durum ON fason_irsaliye(durum_id);
            CREATE INDEX IF NOT EXISTS idx_firma ON fason_irsaliye(firma_id);
        """)
        _tan("2. executescript (3 index) tamam")

        c = conn.execute("SELECT COUNT(*) FROM fason_durum").fetchone()
        _tan("SELECT COUNT(fason_durum) tamam")
        if c[0] == 0:
            varsayilan_durumlar = [
                ("Fatura kaydı bekleniyor", "amber",  "fa-hourglass-half", 10),
                ("Çıkış yok",               "red",    "fa-ban",             20),
                ("Kısmi çıkış",             "purple", "fa-clock-rotate-left", 30),
                ("Çıkış yapıldı",           "green",  "fa-check",           40),
            ]
            conn.executemany(
                "INSERT INTO fason_durum (ad, renk, ikon, sira) VALUES (?, ?, ?, ?)",
                varsayilan_durumlar
            )
            print(f"[Fason DB] Varsayilan 4 durum eklendi")

        if surum < 2:
            # Önceki OTIS çıkış yüklemeleri OTIS (kantar) kilosunu yazmıştı → SAP ölçeğine çevir (bir kez)
            n = conn.execute("""
                UPDATE fason_kalem_cikis
                SET miktar = ROUND(miktar * (SELECT k.giris_miktari / k.otis_giris FROM fason_kalem k WHERE k.id = fason_kalem_cikis.kalem_id), 3)
                WHERE kaynak = 'otis' AND miktar IS NOT NULL
                  AND kalem_id IN (SELECT id FROM fason_kalem WHERE giris_miktari > 0 AND otis_giris > 0
                                   AND ABS(giris_miktari - otis_giris) > 0.01)
            """).rowcount
            if n:
                print(f"[Fason DB] {n} OTIS çıkış hareketi SAP ölçeğine (kantar oranı) çevrildi", flush=True)
        if surum < 5:
            if "alici" not in _mevcut_kolonlar(conn, "fason_otis_cikis_satir"):
                conn.execute("ALTER TABLE fason_otis_cikis_satir ADD COLUMN alici TEXT DEFAULT ''")
        if surum < 4:
            if "siparis" not in _mevcut_kolonlar(conn, "fason_otis_cikis_satir"):
                conn.execute("ALTER TABLE fason_otis_cikis_satir ADD COLUMN siparis TEXT DEFAULT ''")
            conn.executemany("INSERT OR IGNORE INTO fason_sap_cikis_ayar (yer, siparis, musteri, alici, firma) VALUES (?, ?, ?, ?, ?)", [
                ("ICN", "END-T-000017", "2400", "MEHMET CURA", "IC İÇTAŞ NÜKLEER"),
                ("TSM ENERJİ", "END-T-000016", "1000049950", "SEZER YAMAN", "TSM ENERJİ"),
            ])
        if surum < 3:
            # Eski "giriş bekleyen" satırlarını ham OTIS çıkış tablosuna taşı (bekleyen listesi artık oradan türetiliyor)
            try:
                conn.execute("""
                    INSERT INTO fason_otis_cikis_satir (cikis_no, giris_no, sap_kodu, tanim, kg, tarih, yer)
                    SELECT b.cikis_no, b.giris_no, b.sap_kodu, b.malzeme_tanim, b.miktar, b.tarih, b.yer
                    FROM fason_otis_cikis_bekleyen b
                    WHERE NOT EXISTS (SELECT 1 FROM fason_otis_cikis_satir s WHERE s.cikis_no = b.cikis_no)
                """)
            except Exception as e_:
                print(f"[Fason DB] bekleyen taşıma atlandı: {e_}", flush=True)
        conn.execute(f"PRAGMA user_version = {_FASON_SEMA_SURUMU}")
        conn.commit()
        _tan("commit tamam")
        print(f"[Fason DB] Tablolar hazir: {db_yol} (şema sürümü {_FASON_SEMA_SURUMU} — sonraki açılışlarda kontroller atlanır)")
    except Exception as e:
        print(f"[Fason DB] Init hatasi: {e}")
    finally:
        conn.close()


def init_fason_db():
    _init_fason_db_at(DB_YOL)
    try:
        if os.path.isdir(ORTAK_KLASOR):
            ortak_db_yol = os.path.join(ORTAK_KLASOR, "fason.db")
            _init_fason_db_at(ortak_db_yol)
    except Exception as e:
        print(f"[Fason DB] Ortak (K:) veritabanı hazırlanamadı: {e}")


# ═════════════════════════════════════════════════
# FİRMA ENDPOINT'LERİ
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/firmalar", methods=["GET"])
def api_fason_firmalar():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    tumu = request.args.get("tumu") == "1"
    conn = get_db()
    try:
        if tumu:
            rows = conn.execute("SELECT * FROM fason_firma ORDER BY ad").fetchall()
        else:
            rows = conn.execute("SELECT * FROM fason_firma WHERE aktif = 1 ORDER BY ad").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/firma-ekle", methods=["POST"])
def api_fason_firma_ekle():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        d = request.get_json() or {}
        ad = (d.get("ad") or "").strip()
        if not ad:
            return jsonify({"durum": "hata", "mesaj": "Firma adı zorunlu"}), 400
        if len(ad) > 100:
            return jsonify({"durum": "hata", "mesaj": "Firma adı çok uzun"}), 400
        conn = get_db()
        try:
            var = conn.execute("SELECT id FROM fason_firma WHERE LOWER(ad) = LOWER(?)", (ad,)).fetchone()
            if var:
                return jsonify({"durum": "hata", "mesaj": "Bu firma zaten kayıtlı", "id": var["id"]}), 400
            cur = conn.execute("INSERT INTO fason_firma (ad) VALUES (?)", (ad,))
            conn.commit()
            return jsonify({"durum": "ok", "id": cur.lastrowid, "ad": ad, "mesaj": f"{ad} eklendi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/firma-sil/<int:fid>", methods=["DELETE"])
def api_fason_firma_sil(fid):
    if not _fason_yetki_var_mi("fason_duzenle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        conn = get_db()
        try:
            c = conn.execute("SELECT COUNT(*) FROM fason_irsaliye WHERE firma_id = ?", (fid,)).fetchone()[0]
            if c > 0:
                conn.execute("UPDATE fason_firma SET aktif = 0 WHERE id = ?", (fid,))
                conn.commit()
                return jsonify({"durum": "ok", "mesaj": f"Pasifleştirildi ({c} irsaliyede kullanılıyor)"})
            else:
                conn.execute("DELETE FROM fason_firma WHERE id = ?", (fid,))
                conn.commit()
                return jsonify({"durum": "ok", "mesaj": "Silindi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# İRSALİYE ENDPOINT'LERİ
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/liste", methods=["GET"])
def api_fason_liste():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                i.id, i.irsaliye_no, i.aciklama,
                i.giren_kullanici, i.girilme_tarihi,
                i.irsaliye_tarihi, i.firma_id,
                f.ad AS firma_ad,
                k.toplam_stok, k.toplam_giris, k.toplam_otis_stok, k.toplam_otis_giris,
                k.toplam_fiyat_sum,
                COALESCE(k.kalem_sayisi, 0) AS kalem_sayisi,
                COALESCE(k.belirsiz_kalem, 0) AS belirsiz_kalem,
                k.giris_tarihi, k.giris_tarihi_son, COALESCE(k.giris_tarih_sayisi, 0) AS giris_tarih_sayisi,
                COALESCE(k.durum_hesaplanan, 'Stok') AS durum_hesaplanan
            FROM fason_irsaliye i
            LEFT JOIN fason_firma f ON i.firma_id = f.id
            LEFT JOIN (
                SELECT irsaliye_id,
                       SUM(stok_miktari) AS toplam_stok,
                       SUM(giris_miktari) AS toplam_giris,
                       SUM(otis_stok) AS toplam_otis_stok,
                       SUM(otis_giris) AS toplam_otis_giris,
                       SUM(toplam_fiyat) AS toplam_fiyat_sum,
                       COUNT(*) AS kalem_sayisi,
                       SUM(CASE WHEN durum IS NULL OR durum = '' THEN 1 ELSE 0 END) AS belirsiz_kalem,
                       -- Giriş tarihi = ZMM068 "Belge tarihi" (tek doğru kaynak; elle girilen irsaliye tarihi kullanılmaz)
                       MIN(NULLIF(belge_tarihi, '')) AS giris_tarihi,
                       MAX(NULLIF(belge_tarihi, '')) AS giris_tarihi_son,
                       COUNT(DISTINCT NULLIF(belge_tarihi, '')) AS giris_tarih_sayisi,
                       CASE WHEN COUNT(DISTINCT durum) = 1 THEN MAX(durum) ELSE 'Kısmi Çıkış' END AS durum_hesaplanan
                FROM fason_kalem GROUP BY irsaliye_id
            ) k ON k.irsaliye_id = i.id
            ORDER BY i.id DESC
        """).fetchall()
        try:
            ekler = _kart_ekleri(conn)
        except Exception as e_:                      # kart ekleri hesaplanamazsa liste yine gelsin
            print(f"[Fason] kart ekleri hesaplanamadı: {e_}", flush=True)
            ekler = {}
        # SAP Stok = SAP Giriş − SAP çıkışı (hesaplanan; elle girilen stok kullanılmaz)
        irs_stok = {}
        try:
            stoklar = _sap_stoklari(conn)
            for k in conn.execute("SELECT id, irsaliye_id, giris_miktari FROM fason_kalem").fetchall():
                v, _ = _sap_stok_degeri(k, stoklar)
                if v is not None:
                    irs_stok[k["irsaliye_id"]] = irs_stok.get(k["irsaliye_id"], 0.0) + v
        except Exception as e_:
            print(f"[Fason] SAP stok hesaplanamadı: {e_}", flush=True)
            irs_stok = None
        sonuc = []
        for r in rows:
            d = dict(r)
            d["kart"] = ekler.get(r["id"])
            if irs_stok is not None:
                d["toplam_stok"] = round(irs_stok[r["id"]], 3) if r["id"] in irs_stok else None
            sonuc.append(d)
        return jsonify(sonuc)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


def _sap_stoklari(conn, sap_kodlari=None):
    """SAP Stok (giriş irsaliyesi bazında) = SAP Giriş − bu girişe istinaden SAP'de yapılmış çıkış.
    Fason giriş irsaliyesi bazlı çalışıyoruz; SAP çıkışı ise giriş bilmez (malzeme + çıkış no toplamı).
    Bu yüzden her kalemin sistemdeki Satış/Çıkış/A63 hareketi (= o girişin payı, OTIS çıkıştan gelir) için:
      SAP'de bu çıkış no + SAP kodu ile yapılan toplam, OTIS'te o çıkışın TÜM girişlerdeki toplamıyla karşılaştırılır.
      SAP ≈ OTIS toplamı (ya da fazlası) → payın tamamı düşülür; SAP daha azsa pay o oranda düşülür; SAP'de yoksa düşülmez.
    Nakil (Akkuyu) ve tadilat SAP'de yapılmadığı için hiç hesaba girmez (malzeme stokta kalır).
    Döner: {kalem_id: {"sap_cikis": kg, "sap_stok": kg | None}} (sadece SAP kodu olan kalemler)."""
    if sap_kodlari is not None:
        sap_kodlari = [k for k in set(sap_kodlari) if k]
        if not sap_kodlari:
            return {}

    def parcali(sql_sablon, liste):
        if liste is None:
            return conn.execute(sql_sablon.format(ek="")).fetchall()
        sonuc_ = []
        for i in range(0, len(liste), 900):
            p_ = liste[i:i + 900]
            sonuc_ += conn.execute(sql_sablon.format(ek=f" AND k.sap_kodu IN ({','.join('?' * len(p_))})"), p_).fetchall()
        return sonuc_

    kalemler = parcali("SELECT k.id, k.sap_kodu, k.giris_miktari FROM fason_kalem k WHERE COALESCE(k.sap_kodu, '') <> ''{ek}", sap_kodlari)
    if not kalemler:
        return {}
    sonuc = {r["id"]: {"sap_cikis": 0.0, "giris": r["giris_miktari"]} for r in kalemler}
    hareketler = parcali("""
        SELECT c.kalem_id, k.sap_kodu, c.irsaliye_no, c.miktar
        FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id
        WHERE c.irsaliye_no <> '' AND c.miktar IS NOT NULL AND COALESCE(c.tip, 'cikis') IN ('cikis', 'a63')
          AND COALESCE(k.sap_kodu, '') <> ''{ek}
    """, sap_kodlari)
    if hareketler:
        nolar = {h["irsaliye_no"] for h in hareketler}
        sap = _sap_toplamlari(conn, nolar)
        if sap:
            otis = _otis_cikis_toplamlari(conn, nolar)
            bizde_top = {}
            for h in hareketler:
                a = (h["sap_kodu"], h["irsaliye_no"])
                bizde_top[a] = bizde_top.get(a, 0.0) + (h["miktar"] or 0.0)
            for h in hareketler:
                a = (h["sap_kodu"], h["irsaliye_no"])
                s_ = sap.get(a)
                if not s_:
                    continue
                o_ = otis.get(a)
                adaylar = [bizde_top.get(a)] + (list(o_) if isinstance(o_, (tuple, list)) else [o_])
                ref = _otis_ref(tuple(c for c in adaylar if c is not None) or None, s_["miktar"])
                sonuc[h["kalem_id"]]["sap_cikis"] += (h["miktar"] or 0.0) * _sap_pay_orani(s_["miktar"], ref)
    for d in sonuc.values():
        d["sap_cikis"] = round(d["sap_cikis"], 3)
        d["sap_stok"] = round(d["giris"] - d["sap_cikis"], 3) + 0.0 if d["giris"] is not None else None
        d.pop("giris", None)
    return sonuc


def _sap_stok_degeri(kalem_row, stoklar):
    """Kalemin gösterilecek SAP Stok'u: SAP kodu varsa hesaplanan, yoksa giriş (SAP'de çıkışı bilinemez)."""
    d = stoklar.get(kalem_row["id"])
    if d:
        return d["sap_stok"], d["sap_cikis"]
    g = kalem_row["giris_miktari"]
    return (g, 0.0) if g is not None else (None, None)


def _kart_ekleri(conn):
    """Liste kartları için irsaliye bazında: konum dağılımı (çıkan / tadilatta / nakilde / depoda kg),
    son kesin çıkış tarihi ve uyarı sayıları (eksik hareket, SAP'de yapılmamış, miktar/tarih eksik, girişten fazla çıkış)."""
    kesin_durum = ("Çıkış", "Çıkış (A63)")
    ekler = {}

    def ek(irs_id):
        e = ekler.get(irs_id)
        if e is None:
            e = ekler[irs_id] = {"giris": 0.0, "kesin": 0.0, "tadilatta": 0.0, "nakilde": 0.0, "depoda": 0.0,
                                 "tahmini_kg": 0.0, "son_cikis": None,
                                 "u": {"eksik": 0, "sap_yok": 0, "sap_fark": 0, "miktarsiz": 0, "tarihsiz": 0, "fazla": 0}}
        return e

    for k in _kalem_hareket_ozetleri(conn, order_sql="k.id"):
        e = ek(k["irsaliye_id"])
        giris = k["giris"] or 0.0
        e["giris"] += giris
        if k["hareket_sayisi"] and not k["miktari_eksik"] and k["giris"] is not None:
            e["kesin"] += max(0.0, giris - (k["kalan"] or 0.0)) if k["kalan"] is not None else 0.0
            e["tadilatta"] += k["tadilatta"] or 0.0
            e["nakilde"] += k["nakilde"] or 0.0
            e["depoda"] += max(0.0, k["depoda"] or 0.0)
            if k["kalan"] is not None and k["kalan"] < -0.01:
                e["u"]["fazla"] += 1
        else:
            # Hareketi yok / miktarı eksik → kalemin durumundan tahmin
            konum = _DURUM_KONUM.get(k["durum"], "depoda")
            e[konum if konum != "kesin" else "kesin"] += giris
            if k["durum"]:
                e["tahmini_kg"] += giris
        # Eksik hareket — "Eksik Hareketler" ekranıyla aynı kurallar
        eksik = False
        if k["otis_cikan"] is not None and k["durum"] != "Stok":     # "Stok" = depoda olduğu teyit edildi
            tol = max(_OTIS_TOL_KG, _OTIS_TOL_ORAN * (k["giris"] or k["otis_giris"] or 0))
            eksik = (k["otis_cikan_sap"] - k["kayitli_cikan"]) > tol   # kantar farkından arındırılmış
        if k["durum"] in kesin_durum and not k["hareket_sayisi"]:
            eksik = True
        elif (k["durum"] in kesin_durum and k["hareket_sayisi"] and not k["miktari_eksik"]
              and k["kalan"] is not None and k["kalan"] > 0.005):
            eksik = True
        if eksik:
            e["u"]["eksik"] += 1

    # Son kesin çıkış tarihi + miktarı / tarihi girilmemiş hareketler
    for r in conn.execute("""
        SELECT k.irsaliye_id,
               MAX(CASE WHEN COALESCE(c.tip,'cikis') IN ('cikis','a63') THEN c.tarih END) AS son_cikis,
               SUM(CASE WHEN c.miktar IS NULL AND (c.irsaliye_no <> '' OR c.tip = 'tadilat_donus') THEN 1 ELSE 0 END) AS miktarsiz,
               SUM(CASE WHEN COALESCE(c.tarih, '') = '' THEN 1 ELSE 0 END) AS tarihsiz
        FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id
        GROUP BY k.irsaliye_id
    """).fetchall():
        e = ek(r["irsaliye_id"])
        e["son_cikis"] = r["son_cikis"]
        e["u"]["miktarsiz"] = r["miktarsiz"] or 0
        e["u"]["tarihsiz"] = r["tarihsiz"] or 0

    # SAP'de yapılmamış / miktar farkı (SAP çıkış verisi yüklüyse, kapsamındaki hareketler için)
    kapsam = _sap_kapsam(conn)
    if kapsam["satir"]:
        sap = _sap_toplamlari(conn)
        sap_nolari = {no for (_, no) in sap.keys()}
        otis_top = _otis_cikis_toplamlari(conn, sap_nolari)
        satirlar_ = conn.execute("""
            SELECT k.irsaliye_id, k.sap_kodu, c.irsaliye_no, SUM(COALESCE(c.miktar, 0)) AS miktar, MAX(c.tarih) AS tarih
            FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id
            WHERE c.irsaliye_no <> '' AND COALESCE(c.tip, 'cikis') IN ('cikis', 'a63') AND COALESCE(k.sap_kodu, '') <> ''   -- nakil / tadilat SAP'de yapılmıyor
            GROUP BY k.irsaliye_id, k.sap_kodu, c.irsaliye_no
        """).fetchall()
        # SAP çıkışı giriş bilmez: aynı SAP kodu + çıkış no'lu tüm girişlerimizin toplamıyla karşılaştırılır
        sistem_top = {}
        for r in satirlar_:
            a_ = (r["sap_kodu"], r["irsaliye_no"])
            sistem_top[a_] = sistem_top.get(a_, 0.0) + (r["miktar"] or 0.0)
        for r in satirlar_:
            kapsamda = _sap_kapsamda(kapsam, r["tarih"]) or r["irsaliye_no"] in sap_nolari
            a_ = (r["sap_kodu"], r["irsaliye_no"])
            durum = _sap_durum(round(sistem_top[a_], 3), sap.get(a_), kapsamda, otis_top.get(a_))
            if durum == "sap_yok":
                ek(r["irsaliye_id"])["u"]["sap_yok"] += 1
            elif durum == "miktar_farki":
                ek(r["irsaliye_id"])["u"]["sap_fark"] += 1

    for e in ekler.values():
        for a in ("giris", "kesin", "tadilatta", "nakilde", "depoda", "tahmini_kg"):
            e[a] = round(e[a], 2)
    return ekler


@fason_bp.route("/api/fason/ekle", methods=["POST"])
def api_fason_ekle():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_ekle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        d = request.get_json() or {}
        irsaliye_no = (d.get("irsaliye_no") or "").strip()
        firma_id = d.get("firma_id")
        aciklama = (d.get("aciklama") or "").strip()

        if not irsaliye_no:
            return jsonify({"durum": "hata", "mesaj": "İrsaliye No zorunlu"}), 400
        if len(irsaliye_no) > 50:
            return jsonify({"durum": "hata", "mesaj": "İrsaliye No çok uzun"}), 400
        if not firma_id:
            return jsonify({"durum": "hata", "mesaj": "Firma seçiniz"}), 400
        try:
            firma_id = int(firma_id)
        except:
            return jsonify({"durum": "hata", "mesaj": "Geçersiz firma"}), 400

        conn = get_db()
        try:
            fv = conn.execute("SELECT id, ad FROM fason_firma WHERE id = ?", (firma_id,)).fetchone()
            if not fv:
                return jsonify({"durum": "hata", "mesaj": "Firma bulunamadı"}), 400

            mevcut = conn.execute(
                "SELECT id FROM fason_irsaliye WHERE irsaliye_no = ?",
                (irsaliye_no,)
            ).fetchone()

            if mevcut and not _fason_yetki_var_mi("fason_mukerrer"):
                return jsonify({
                    "durum": "hata",
                    "mesaj": f"Bu irsaliye numarası zaten kayıtlı: {irsaliye_no}"
                }), 400

            cur = conn.execute("""
                INSERT INTO fason_irsaliye
                    (irsaliye_no, aciklama, giren_kullanici, firma_id)
                VALUES (?, ?, ?, ?)
            """, (irsaliye_no, aciklama, session.get("kullanici", "-"), firma_id))
            conn.commit()

            log_kaydet(
                "İrsaliye Ekleme",
                f"{irsaliye_no} — {fv['ad']}" + (" (mükerrer no — admin tarafından eklendi)" if mevcut else ""),
                cur.lastrowid,
                irsaliye_no
            )

            return jsonify({
                "durum": "ok",
                "id": cur.lastrowid,
                "mesaj": f"İrsaliye eklendi: {irsaliye_no}",
                "uyari": ("Bu irsaliye numarası daha önce girilmişti (admin olarak eklendi)" if mevcut else None)
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

@fason_bp.route("/api/fason/irsaliye/<int:irs_id>/kalemler", methods=["GET"])
def api_fason_kalem_liste(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT k.*,
                   COALESCE(c.cikis_sayisi, 0) AS cikis_sayisi,
                   COALESCE(c.kesin, 0) AS h_kesin, COALESCE(c.gidis, 0) AS h_gidis,
                   COALESCE(c.donus, 0) AS h_donus, COALESCE(c.nakil, 0) AS h_nakil,
                   COALESCE(c.miktari_eksik, 0) AS cikis_miktari_eksik
            FROM fason_kalem k
            LEFT JOIN (
                SELECT kalem_id, COUNT(*) AS cikis_sayisi,
                       SUM(CASE WHEN COALESCE(tip,'cikis') IN ('cikis','a63') THEN COALESCE(miktar,0) ELSE 0 END) AS kesin,
                       SUM(CASE WHEN tip = 'tadilat_gidis' THEN COALESCE(miktar,0) ELSE 0 END) AS gidis,
                       SUM(CASE WHEN tip = 'tadilat_donus' THEN COALESCE(miktar,0) ELSE 0 END) AS donus,
                       SUM(CASE WHEN tip = 'nakil' THEN COALESCE(miktar,0) ELSE 0 END) AS nakil,
                       MAX(CASE WHEN miktar IS NULL THEN 1 ELSE 0 END) AS miktari_eksik
                FROM fason_kalem_cikis GROUP BY kalem_id
            ) c ON c.kalem_id = k.id
            WHERE k.irsaliye_id = ? ORDER BY k.id
        """, (irs_id,)).fetchall()
        stoklar = _sap_stoklari(conn, [r["sap_kodu"] for r in rows])
        sonuc = []
        for r in rows:
            d = dict(r)
            for a in ("h_kesin", "h_gidis", "h_donus", "h_nakil"):
                d.pop(a, None)
            d["stok_elle"] = d.get("stok_miktari")
            d["stok_miktari"], d["sap_cikis"] = _sap_stok_degeri(r, stoklar)
            d["cikis_kalan"] = d["cikis_tadilatta"] = d["cikis_nakilde"] = None
            if r["cikis_sayisi"]:
                oz = _hareket_hesapla(r["giris_miktari"], r["h_kesin"], r["h_gidis"], r["h_donus"], r["h_nakil"],
                                      r["cikis_miktari_eksik"])
                d["cikis_kalan"] = oz["kalan"]          # giriş - kesin çıkış (henüz kesin çıkmamış miktar)
                d["cikis_tadilatta"] = oz["tadilatta"]
                d["cikis_nakilde"] = oz["nakilde"]
                d["toplam_cikan"] = oz["kesin_cikan"]
            sonuc.append(d)
        return jsonify(sonuc)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


# ═════════════════════════════════════════════════
# KALEM ÇIKIŞ İRSALİYELERİ — bir kalem birden fazla irsaliyeyle (parça parça) çıkabilir
# ═════════════════════════════════════════════════

def _cikis_miktar_parse(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        try:
            return float(str(v).replace(".", "").replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError("Geçersiz miktar")


@fason_bp.route("/api/fason/kalem/<int:kalem_id>/cikislar", methods=["GET"])
def api_fason_kalem_cikislar(kalem_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        k = conn.execute("""
            SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.stok_miktari, k.durum, i.irsaliye_no,
                   NULLIF(k.belge_tarihi, '') AS giris_tarihi
            FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE k.id = ?
        """, (kalem_id,)).fetchone()
        if not k:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404
        cikislar = conn.execute("""
            SELECT id, irsaliye_no, miktar, tarih, kaynak, ekleyen, olusturma, COALESCE(tip, 'cikis') AS tip
            FROM fason_kalem_cikis WHERE kalem_id = ? ORDER BY COALESCE(tarih, ''), id
        """, (kalem_id,)).fetchall()
        sap_cikislar, sap_paylasan = [], 0
        if k["sap_kodu"]:
            sap_cikislar = [dict(r) for r in conn.execute("""
                SELECT cikis_no, ROUND(SUM(COALESCE(miktar, 0)), 3) AS miktar, MAX(belge_tarihi) AS tarih,
                       GROUP_CONCAT(DISTINCT malzeme_belgesi) AS belgeler, GROUP_CONCAT(DISTINCT islem_turu) AS islem
                FROM fason_sap_cikis WHERE sap_kodu = ? GROUP BY cikis_no ORDER BY MAX(belge_tarihi), cikis_no
            """, (k["sap_kodu"],)).fetchall() if abs(r["miktar"] or 0) > _SAP_TOL]
            sap_paylasan = conn.execute("SELECT COUNT(*) FROM fason_kalem WHERE sap_kodu = ?", (k["sap_kodu"],)).fetchone()[0]
            otis_top = _otis_cikis_toplamlari(conn, {x["cikis_no"] for x in sap_cikislar})
            sistem_top = {r[0]: r[1] or 0.0 for r in conn.execute("""
                SELECT c.irsaliye_no, SUM(COALESCE(c.miktar, 0)) FROM fason_kalem_cikis c JOIN fason_kalem kk ON kk.id = c.kalem_id
                WHERE kk.sap_kodu = ? AND COALESCE(c.tip, 'cikis') IN ('cikis', 'a63') AND c.irsaliye_no <> ''
                GROUP BY c.irsaliye_no""", (k["sap_kodu"],)).fetchall()}
            for x in sap_cikislar:
                o_ = otis_top.get((k["sap_kodu"], x["cikis_no"]))
                x["otis_toplam"] = _otis_ref(o_, x["miktar"])
                x["sistem_toplam"] = round(sistem_top.get(x["cikis_no"], 0.0), 3)
                x["durum"] = _sap_durum(x["sistem_toplam"] or None, x, True, o_)
        return jsonify({"kalem": dict(k), "cikislar": [dict(c) for c in cikislar],
                        "ozet": _cikis_ozet_hesapla(conn, kalem_id), "tipler": HAREKET_TIPLERI,
                        "sap_cikislar": sap_cikislar, "sap_kodu_kalem_sayisi": sap_paylasan,
                        "sap_veri_var": bool(conn.execute("SELECT 1 FROM fason_sap_cikis LIMIT 1").fetchone())})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem/<int:kalem_id>/cikis", methods=["POST"])
def api_fason_kalem_cikis_ekle(kalem_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}
    tip = (d.get("tip") or "cikis").strip()
    if tip not in HAREKET_TIPLERI:
        return jsonify({"durum": "hata", "mesaj": "Geçersiz hareket tipi"}), 400
    no = (d.get("irsaliye_no") or "").strip()
    if not no and tip != "tadilat_donus":
        return jsonify({"durum": "hata", "mesaj": "İrsaliye no zorunlu"}), 400
    if len(no) > 50:
        return jsonify({"durum": "hata", "mesaj": "İrsaliye no çok uzun"}), 400
    try:
        miktar = _cikis_miktar_parse(d.get("miktar"))
    except ValueError as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 400
    if miktar is not None and miktar <= 0:
        return jsonify({"durum": "hata", "mesaj": "Miktar 0'dan büyük olmalı"}), 400
    tarih = (d.get("tarih") or "").strip() or None

    conn = get_db()
    try:
        k = conn.execute("SELECT id, malzeme_tanim, durum FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
        if not k:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404
        if no and conn.execute("SELECT 1 FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?", (kalem_id, no)).fetchone():
            return jsonify({"durum": "hata", "mesaj": f"{no} bu kalemde zaten var — listeden düzenleyebilirsin"}), 409
        _cikis_ekle(conn, kalem_id, no, miktar, tarih, "elle", session.get("kullanici", ""), tip)
        _cikis_ozet_guncelle(conn, kalem_id)
        # Durumu harekete göre güncelle (tamamı çıktı → Çıkış, tamamı tadilatta → Tadilat, döndü → Stok...)
        durum_degisti = _hareket_sonrasi_durum(conn, kalem_id, tip)
        ozet = _cikis_ozet_hesapla(conn, kalem_id)
        conn.commit()
        log_kaydet("Kalem Hareketi Ekleme",
                   f"{k['malzeme_tanim']}: {HAREKET_TIPLERI[tip]} {no}".rstrip() + (f" ({miktar:g})" if miktar is not None else ""),
                   kalem_id, no)
        return jsonify({"durum": "ok", "mesaj": f"{HAREKET_TIPLERI[tip]} eklendi", "ozet": ozet, "durum_degisti": durum_degisti})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem-cikis/<int:cikis_id>", methods=["PUT", "DELETE"])
def api_fason_kalem_cikis_duzenle(cikis_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    conn = get_db()
    try:
        c = conn.execute("""
            SELECT c.*, k.malzeme_tanim FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id WHERE c.id = ?
        """, (cikis_id,)).fetchone()
        if not c:
            return jsonify({"durum": "hata", "mesaj": "Çıkış kaydı bulunamadı"}), 404

        if request.method == "DELETE":
            conn.execute("DELETE FROM fason_kalem_cikis WHERE id = ?", (cikis_id,))
            _cikis_ozet_guncelle(conn, c["kalem_id"])
            conn.commit()
            log_kaydet("Çıkış İrsaliyesi Silme", f"{c['malzeme_tanim']}: {c['irsaliye_no']}", c["kalem_id"], c["irsaliye_no"])
            return jsonify({"durum": "ok", "mesaj": "Çıkış irsaliyesi silindi", "ozet": _cikis_ozet_hesapla(conn, c["kalem_id"])})

        d = request.get_json() or {}
        set_parts, vals = [], []
        yeni_tip = (d.get("tip") or "").strip() if "tip" in d else (c["tip"] or "cikis")
        if yeni_tip not in HAREKET_TIPLERI:
            return jsonify({"durum": "hata", "mesaj": "Geçersiz hareket tipi"}), 400
        if "tip" in d:
            set_parts.append("tip = ?"); vals.append(yeni_tip)
        if "irsaliye_no" in d:
            no = (d.get("irsaliye_no") or "").strip()
            if not no and yeni_tip != "tadilat_donus":
                return jsonify({"durum": "hata", "mesaj": "İrsaliye no boş olamaz"}), 400
            if no and no != c["irsaliye_no"] and conn.execute(
                    "SELECT 1 FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?", (c["kalem_id"], no)).fetchone():
                return jsonify({"durum": "hata", "mesaj": f"{no} bu kalemde zaten var"}), 409
            set_parts.append("irsaliye_no = ?"); vals.append(no)
        if "miktar" in d:
            try:
                m = _cikis_miktar_parse(d.get("miktar"))
            except ValueError as e:
                return jsonify({"durum": "hata", "mesaj": str(e)}), 400
            if m is not None and m <= 0:
                return jsonify({"durum": "hata", "mesaj": "Miktar 0'dan büyük olmalı"}), 400
            set_parts.append("miktar = ?"); vals.append(m)
        if "tarih" in d:
            set_parts.append("tarih = ?"); vals.append((d.get("tarih") or "").strip() or None)
        if not set_parts:
            return jsonify({"durum": "hata", "mesaj": "Güncellenecek alan yok"}), 400
        vals.append(cikis_id)
        conn.execute(f"UPDATE fason_kalem_cikis SET {', '.join(set_parts)} WHERE id = ?", vals)
        _cikis_ozet_guncelle(conn, c["kalem_id"])
        durum_degisti = _hareket_sonrasi_durum(conn, c["kalem_id"], yeni_tip) if ("tip" in d or "miktar" in d) else None
        conn.commit()
        return jsonify({"durum": "ok", "mesaj": "Güncellendi", "ozet": _cikis_ozet_hesapla(conn, c["kalem_id"]),
                        "durum_degisti": durum_degisti})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem-ekle/<int:irs_id>", methods=["POST"])
def api_fason_kalem_ekle(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_ekle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}
    malzeme_tanim = (d.get("malzeme_tanim") or "").strip()
    if not malzeme_tanim:
        return jsonify({"durum": "hata", "mesaj": "Malzeme tanımı zorunlu"}), 400

    def sayi(v):
        if v in (None, ""): return None
        try: return float(v)
        except Exception: return None

    conn = get_db()
    try:
        irs = conn.execute("SELECT id FROM fason_irsaliye WHERE id = ?", (irs_id,)).fetchone()
        if not irs:
            return jsonify({"durum": "hata", "mesaj": "İrsaliye bulunamadı"}), 404

        cur = conn.execute("""
            INSERT INTO fason_kalem
                (irsaliye_id, malzeme_tanim, sap_kodu, irsaliye_tarihi, stok_miktari,
                 giris_miktari, otis_stok, otis_giris, toplam_fiyat, para_birimi,
                 birim_fiyat, belge_tarihi, fark_sebebi, durum, cikis_irsaliye_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            irs_id, malzeme_tanim, (d.get("sap_kodu") or "").strip(),
            d.get("irsaliye_tarihi"), sayi(d.get("stok_miktari")), sayi(d.get("giris_miktari")),
            sayi(d.get("otis_stok")), sayi(d.get("otis_giris")), sayi(d.get("toplam_fiyat")),
            (d.get("para_birimi") or "").strip(), sayi(d.get("birim_fiyat")),
            d.get("belge_tarihi"), (d.get("fark_sebebi") or "").strip(),
            (d.get("durum") or "").strip(), ""
        ))
        for no in _cikis_no_listesi(d.get("cikis_irsaliye_no")):
            _cikis_ekle(conn, cur.lastrowid, no, None, kaynak="elle", kullanici=session.get("kullanici", ""),
                        tip=DURUM_HAREKET_TIPI.get((d.get("durum") or "").strip(), "cikis"))
        _cikis_ozet_guncelle(conn, cur.lastrowid)
        conn.commit()
        log_kaydet("Kalem Ekleme", f"{malzeme_tanim}", cur.lastrowid, malzeme_tanim)
        return jsonify({"durum": "ok", "id": cur.lastrowid, "mesaj": "Kalem eklendi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem-guncelle/<int:kalem_id>", methods=["POST"])
def api_fason_kalem_guncelle(kalem_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}

    def sayi(v):
        if v in (None, ""): return None
        try: return float(v)
        except Exception: return None

    # cikis_irsaliye_no artık doğrudan yazılmaz: gelen numara(lar) çıkış listesine EKLENİR
    # (eskileri silinmez — bir kalem birden fazla irsaliyeyle çıkabilir). Boş değer yok sayılır.
    alanlar = ["malzeme_tanim", "sap_kodu", "irsaliye_tarihi", "stok_miktari", "giris_miktari",
               "otis_stok", "otis_giris", "toplam_fiyat", "para_birimi", "birim_fiyat",
               "belge_tarihi", "fark_sebebi", "durum"]
    sayisal = {"stok_miktari", "giris_miktari", "otis_stok", "otis_giris", "toplam_fiyat", "birim_fiyat"}
    yeni_cikis_nolar = _cikis_no_listesi(d.get("cikis_irsaliye_no")) if "cikis_irsaliye_no" in d else []

    conn = get_db()
    try:
        mevcut = conn.execute("SELECT * FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
        if not mevcut:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404

        set_parts, vals = [], []
        for a in alanlar:
            if a in d:
                v = sayi(d[a]) if a in sayisal else (d[a] or "").strip() if isinstance(d[a], str) else d[a]
                set_parts.append(f"{a} = ?")
                vals.append(v)
        if "durum" in d and d["durum"] not in ("Stok", "Çıkış", "Çıkış (A63)", "Tadilat", "Nakil", "Aspro"):
            return jsonify({"durum": "hata", "mesaj": "Geçersiz durum değeri"}), 400
        if not set_parts and not yeni_cikis_nolar:
            return jsonify({"durum": "hata", "mesaj": "Güncellenecek alan yok"}), 400

        # Rapor ekranındaki giriş/çıkış trendi durum_tarihi'ne dayanıyor — durum fiilen
        # değiştiyse (aynı değere tekrar set etmek sayılmaz) o anı damgala.
        if "durum" in d and d["durum"] != mevcut["durum"]:
            set_parts.append("durum_tarihi = datetime('now','localtime')")

        if set_parts:
            vals.append(kalem_id)
            conn.execute(f"UPDATE fason_kalem SET {', '.join(set_parts)} WHERE id = ?", vals)

        if yeni_cikis_nolar:
            # Hareket tipi kalemin (yeni) durumundan: Tadilat → tadilat gönderimi, Nakil → nakil, A63 → a63
            tip = DURUM_HAREKET_TIPI.get(d.get("durum") if "durum" in d else (mevcut["durum"] or ""), "cikis")
            # Toplu işlemde tek irsaliye verildiyse: kesin çıkışta henüz çıkmamış miktarın tamamı,
            # tadilat/nakil gönderiminde depodaki miktarın tamamı bu irsaliyeyle gitmiş sayılır
            miktar = None
            yeni_olanlar = [n for n in yeni_cikis_nolar if not conn.execute(
                "SELECT 1 FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?", (kalem_id, n)).fetchone()]
            if d.get("cikis_miktar_kalan") and len(yeni_olanlar) == 1:
                oz = _cikis_ozet_hesapla(conn, kalem_id)
                aday = oz["kalan"] if tip in KESIN_CIKIS_TIPLERI else oz["depoda"]
                if aday is not None and aday > 0.005:
                    miktar = aday
            for no in yeni_olanlar:
                _cikis_ekle(conn, kalem_id, no, miktar,
                            kaynak="toplu" if d.get("cikis_miktar_kalan") else "elle",
                            kullanici=session.get("kullanici", ""), tip=tip)
            _cikis_ozet_guncelle(conn, kalem_id)
        conn.commit()
        return jsonify({"durum": "ok", "mesaj": "Güncellendi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem-sil/<int:kalem_id>", methods=["DELETE"])
def api_fason_kalem_sil(kalem_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_sil"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    conn = get_db()
    try:
        row = conn.execute("SELECT malzeme_tanim FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
        if not row:
            return jsonify({"durum": "hata", "mesaj": "Bulunamadı"}), 404
        conn.execute("DELETE FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,))
        conn.execute("DELETE FROM fason_kalem WHERE id = ?", (kalem_id,))
        conn.commit()
        log_kaydet("Kalem Silme", row["malzeme_tanim"], kalem_id, row["malzeme_tanim"])
        return jsonify({"durum": "ok", "mesaj": "Silindi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()

@fason_bp.route("/api/fason/durum-guncelle/<int:irs_id>", methods=["POST"])
def api_fason_durum_guncelle(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        d = request.get_json() or {}
        durum_id = d.get("durum_id")
        durum_notu = (d.get("durum_notu") or "").strip()

        if durum_id is not None and durum_id != "":
            try:
                durum_id = int(durum_id)
            except:
                return jsonify({"durum": "hata", "mesaj": "Geçersiz durum"}), 400
        else:
            durum_id = None

        conn = get_db()
        try:
            eski = conn.execute("""
                SELECT i.irsaliye_no, d.ad AS eski_durum
                FROM fason_irsaliye i LEFT JOIN fason_durum d ON i.durum_id = d.id
                WHERE i.id = ?
            """, (irs_id,)).fetchone()

            simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
                UPDATE fason_irsaliye
                SET durum_id = ?, durum_notu = ?,
                    durum_guncelleyen = ?, durum_guncelleme_tarihi = ?
                WHERE id = ?
            """, (durum_id, durum_notu, session.get("kullanici", "-"), simdi, irs_id))
            conn.commit()

            if conn.total_changes == 0:
                return jsonify({"durum": "hata", "mesaj": "İrsaliye bulunamadı"}), 404

            yeni_durum_ad = "—"
            if durum_id:
                yd = conn.execute("SELECT ad FROM fason_durum WHERE id = ?", (durum_id,)).fetchone()
                yeni_durum_ad = yd["ad"] if yd else "—"

            if eski:
                log_kaydet(
                    "Durum Güncelleme",
                    f"{eski['irsaliye_no']}: {eski['eski_durum'] or '—'} → {yeni_durum_ad}",
                    irs_id,
                    eski["irsaliye_no"]
                )

            return jsonify({"durum": "ok", "mesaj": "Durum güncellendi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/sil/<int:irs_id>", methods=["DELETE"])
def api_fason_sil(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        conn = get_db()
        try:
            rec = conn.execute("SELECT irsaliye_no, giren_kullanici FROM fason_irsaliye WHERE id = ?", (irs_id,)).fetchone()
            if not rec:
                return jsonify({"durum": "hata", "mesaj": "Bulunamadı"}), 404
            kullanici = session.get("kullanici")
            if not _fason_yetki_var_mi("fason_sil_tumu") and rec["giren_kullanici"] != kullanici:
                return jsonify({"durum": "hata", "mesaj": "Sadece yetkili kullanıcı veya kaydı giren silebilir"}), 403
            conn.execute("DELETE FROM fason_irsaliye WHERE id = ?", (irs_id,))
            conn.commit()
            log_kaydet("İrsaliye Silme", f"{rec['irsaliye_no']} silindi", irs_id, rec["irsaliye_no"])
            return jsonify({"durum": "ok", "mesaj": "Silindi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# DURUM YÖNETİMİ
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/durumlar", methods=["GET"])
def api_fason_durumlar():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    tumu = request.args.get("tumu") == "1"
    conn = get_db()
    try:
        if tumu:
            rows = conn.execute("SELECT * FROM fason_durum ORDER BY sira, id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM fason_durum WHERE aktif = 1 ORDER BY sira, id").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/durum-ekle", methods=["POST"])
def api_fason_durum_ekle():
    if not _fason_yetki_var_mi("fason_duzenle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        d = request.get_json() or {}
        ad = (d.get("ad") or "").strip()
        renk = (d.get("renk") or "text3").strip()
        ikon = (d.get("ikon") or "fa-circle").strip()
        sira = int(d.get("sira") or 100)
        if not ad:
            return jsonify({"durum": "hata", "mesaj": "Ad zorunlu"}), 400
        conn = get_db()
        try:
            var = conn.execute("SELECT id FROM fason_durum WHERE ad = ?", (ad,)).fetchone()
            if var:
                return jsonify({"durum": "hata", "mesaj": "Bu ad zaten var"}), 400
            cur = conn.execute(
                "INSERT INTO fason_durum (ad, renk, ikon, sira) VALUES (?, ?, ?, ?)",
                (ad, renk, ikon, sira)
            )
            conn.commit()
            return jsonify({"durum": "ok", "id": cur.lastrowid, "mesaj": f"{ad} eklendi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/durum-guncelle-tanim/<int:did>", methods=["POST"])
def api_fason_durum_guncelle_tanim(did):
    if not _fason_yetki_var_mi("fason_duzenle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        d = request.get_json() or {}
        conn = get_db()
        try:
            mevcut = conn.execute("SELECT * FROM fason_durum WHERE id = ?", (did,)).fetchone()
            if not mevcut:
                return jsonify({"durum": "hata", "mesaj": "Bulunamadı"}), 404
            ad   = (d.get("ad") or mevcut["ad"]).strip()
            renk = (d.get("renk") or mevcut["renk"]).strip()
            ikon = (d.get("ikon") or mevcut["ikon"]).strip()
            sira = int(d.get("sira") if d.get("sira") is not None else mevcut["sira"])
            aktif = 1 if d.get("aktif") in (True, 1, "1") else (0 if d.get("aktif") in (False, 0, "0") else mevcut["aktif"])
            conn.execute(
                "UPDATE fason_durum SET ad = ?, renk = ?, ikon = ?, sira = ?, aktif = ? WHERE id = ?",
                (ad, renk, ikon, sira, aktif, did)
            )
            conn.commit()
            return jsonify({"durum": "ok", "mesaj": "Güncellendi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/durum-sil/<int:did>", methods=["DELETE"])
def api_fason_durum_sil(did):
    if not _fason_yetki_var_mi("fason_duzenle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        conn = get_db()
        try:
            c = conn.execute("SELECT COUNT(*) FROM fason_irsaliye WHERE durum_id = ?", (did,)).fetchone()[0]
            if c > 0:
                conn.execute("UPDATE fason_durum SET aktif = 0 WHERE id = ?", (did,))
                conn.commit()
                return jsonify({"durum": "ok", "mesaj": f"Pasifleştirildi ({c} irsaliyede kullanılıyor)"})
            else:
                conn.execute("DELETE FROM fason_durum WHERE id = ?", (did,))
                conn.commit()
                return jsonify({"durum": "ok", "mesaj": "Silindi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# EXCEL EXPORT
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/export", methods=["GET", "POST"])
def api_fason_export():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        conn = get_db()
        rows = conn.execute("""
            SELECT
                i.id, i.irsaliye_no, i.aciklama,
                i.giren_kullanici, i.girilme_tarihi,
                i.durum_notu, i.durum_guncelleyen, i.durum_guncelleme_tarihi,
                i.irsaliye_tarihi, i.stok_miktari, i.giris_miktari,
                i.otis_stok, i.otis_giris, i.toplam_fiyat,
                d.ad AS durum_ad,
                f.ad AS firma_ad
            FROM fason_irsaliye i
            LEFT JOIN fason_durum d ON i.durum_id = d.id
            LEFT JOIN fason_firma f ON i.firma_id = f.id
            ORDER BY i.id DESC
        """).fetchall()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "Fason İrsaliyeleri"

        h_font = Font(bold=True, color="FFFFFF", size=11)
        h_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        kilit_fill = PatternFill("solid", fgColor="374151")
        edit_fill = PatternFill("solid", fgColor="065F46")
        border = Border(
            left=Side(style="thin", color="D1D5DB"),
            right=Side(style="thin", color="D1D5DB"),
            top=Side(style="thin", color="D1D5DB"),
            bottom=Side(style="thin", color="D1D5DB")
        )

        basliklar = [
            ("ID",                    "kilit"),
            ("İrsaliye No",           "kilit"),
            ("Firma",                 "kilit"),
            ("Açıklama",              "kilit"),
            ("Durum",                 "kilit"),
            ("Durum Notu",            "kilit"),
            ("Giren Kullanıcı",       "kilit"),
            ("Girilme Tarihi",        "kilit"),
            ("Durum Güncelleyen",     "kilit"),
            ("Durum Güncel. Tarihi",  "kilit"),
            ("İrsaliye Tarihi",       "edit"),
            ("Stok Miktarı",          "edit"),
            ("Giriş Miktarı",         "edit"),
            ("OTIS Stok",             "edit"),
            ("OTIS Giriş",            "edit"),
            ("Toplam Fiyat (USD)",    "edit"),
        ]

        ws.merge_cells("A1:J1")
        ws["A1"] = "SABİT SÜTUNLAR (değiştirmeyin)"
        ws["A1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["A1"].fill = kilit_fill
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("K1:P1")
        ws["K1"] = "DÜZENLENEBİLİR (import ile güncellenir)"
        ws["K1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["K1"].fill = edit_fill
        ws["K1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22

        for col_idx, (baslik, tip) in enumerate(basliklar, start=1):
            cell = ws.cell(row=2, column=col_idx, value=baslik)
            cell.font = h_font
            cell.fill = kilit_fill if tip == "kilit" else edit_fill
            cell.alignment = h_align
            cell.border = border
        ws.row_dimensions[2].height = 32

        genisliker = [6, 18, 22, 22, 22, 25, 14, 16, 16, 18, 14, 12, 12, 12, 12, 16]
        for i, w in enumerate(genisliker, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        thin_border = Border(
            left=Side(style="thin", color="E5E7EB"),
            right=Side(style="thin", color="E5E7EB"),
            top=Side(style="thin", color="E5E7EB"),
            bottom=Side(style="thin", color="E5E7EB")
        )

        for idx, r in enumerate(rows, start=3):
            values = [
                r["id"],
                r["irsaliye_no"],
                r["firma_ad"] or "",
                r["aciklama"] or "",
                r["durum_ad"] or "",
                r["durum_notu"] or "",
                r["giren_kullanici"],
                r["girilme_tarihi"] or "",
                r["durum_guncelleyen"] or "",
                r["durum_guncelleme_tarihi"] or "",
                r["irsaliye_tarihi"] or "",
                r["stok_miktari"],
                r["giris_miktari"],
                r["otis_stok"],
                r["otis_giris"],
                r["toplam_fiyat"],
            ]
            for c_idx, v in enumerate(values, start=1):
                cell = ws.cell(row=idx, column=c_idx, value=v)
                cell.border = thin_border
                if c_idx in (1, 12, 13, 14, 15):
                    cell.alignment = Alignment(horizontal="right")
                    if c_idx != 1 and v is not None:
                        cell.number_format = "#,##0.00"
                elif c_idx == 16 and v is not None:
                    cell.alignment = Alignment(horizontal="right")
                    cell.number_format = '"$"#,##0.00'
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

            if idx % 2 == 0:
                for c_idx in range(1, len(values) + 1):
                    ws.cell(row=idx, column=c_idx).fill = PatternFill("solid", fgColor="F9FAFB")

        ws.freeze_panes = "D3"

        klasor = os.path.join(_fason_export_klasor(), "exports", "excel")
        os.makedirs(klasor, exist_ok=True)
        dosya_adi = f"fason_irsaliyeleri_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        yol = os.path.join(klasor, dosya_adi)
        wb.save(yol)

        try:
            os.startfile(klasor)
        except Exception:
            pass

        return jsonify({
            "durum": "ok",
            "yol": yol,
            "dosya": dosya_adi,
            "kayit": len(rows),
            "mesaj": f"{len(rows)} kayıt Excel'e aktarıldı"
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# EXCEL IMPORT — Sadece 6 kolon güncellenir
# Değişiklik tespiti + İşlem Geçmişi + uyarı listesi
# ═════════════════════════════════════════════════

ALAN_ETIKET = {
    "irsaliye_tarihi": "İrsaliye Tarihi",
    "stok_miktari":    "Stok Miktarı",
    "giris_miktari":   "Giriş Miktarı",
    "otis_stok":       "OTIS Stok",
    "otis_giris":      "OTIS Giriş",
    "toplam_fiyat":    "Toplam Fiyat",
}
# Bu alanlarda değişiklik olursa frontend'e "önemli" (uyarı) olarak işaretlenir
ONEMLI_ALANLAR = {"otis_stok", "otis_giris"}


@fason_bp.route("/api/fason/import", methods=["POST"])
def api_fason_import():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_import"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    dosya = request.files["dosya"]
    if not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya adı boş"}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(dosya, data_only=True)
        ws = wb.active

        def _tr_norm(s):
            return (str(s) if s is not None else "").strip().lower().replace("\u0307", "")

        def _tr_norm(s):
            # Python'un .lower()'ı Türkçe "İ" harfini "i" + görünmez nokta karakterine
            # çevirdiği için (i̇), bu görünmez karakteri temizleyip normal karşılaştırma yapıyoruz
            return (str(s) if s is not None else "").strip().lower().replace("\u0307", "")

        h_row = None
        for row_idx in range(1, 5):
            row = [_tr_norm(c.value) for c in ws[row_idx]]
            if "kalem id" in row:
                h_row = row_idx
                break
        if h_row is None:
            return jsonify({"durum": "hata", "mesaj": "Başlık satırında 'Kalem ID' bulunamadı"}), 400

        basliklar = [_tr_norm(c.value) for c in ws[h_row]]
        col_id = basliklar.index("kalem id") + 1 if "kalem id" in basliklar else -1
        col_stok = basliklar.index("yeni stok") + 1 if "yeni stok" in basliklar else -1
        col_cikis_irs = basliklar.index("çıkış irsaliyesi") + 1 if "çıkış irsaliyesi" in basliklar else -1
        col_durum = basliklar.index("yeni durum") + 1 if "yeni durum" in basliklar else -1
        if col_id < 0 or col_stok < 0:
            return jsonify({"durum": "hata", "mesaj": "Kalem ID / Yeni Stok sütunları bulunamadı"}), 400

        basliklar_raw = [str(c.value or "").strip() for c in ws[h_row]]
        def bul(anahtar_liste):
            for i, b in enumerate(basliklar_raw):
                bl = b.lower()
                for k in anahtar_liste:
                    if k in bl:
                        return i + 1
            return -1

        col_id       = bul(["id"])
        col_irs_tar  = bul(["i̇rsaliye tarihi", "irsaliye tarihi", "irs tarihi"])
        col_stok     = bul(["stok miktarı", "stok miktari"])
        col_giris    = bul(["giriş miktarı", "giris miktari"])
        col_otis_st  = bul(["otis stok"])
        col_otis_gi  = bul(["otis giriş", "otis giris"])
        col_fiyat    = bul(["toplam fiyat", "fiyat"])

        if col_id < 0:
            return jsonify({"durum": "hata", "mesaj": "ID sütunu bulunamadı"}), 400

        eslesenler = {
            "irsaliye_tarihi": col_irs_tar,
            "stok_miktari":    col_stok,
            "giris_miktari":   col_giris,
            "otis_stok":       col_otis_st,
            "otis_giris":      col_otis_gi,
            "toplam_fiyat":    col_fiyat,
        }
        bulunan_kolonlar = [k for k, v in eslesenler.items() if v > 0]
        if not bulunan_kolonlar:
            return jsonify({
                "durum": "hata",
                "mesaj": "Güncellenebilir sütun bulunamadı. Başlıklar: " + ", ".join(basliklar_raw[:20])
            }), 400

        guncellenen = 0
        atlanan = 0
        hatalar = []
        degisiklikler = []  # frontend'e dönecek: [{irsaliye_no, alan, alan_etiket, eski, yeni, onemli}]
        conn = get_db()
        try:
            for row_idx in range(h_row + 1, ws.max_row + 1):
                id_val = ws.cell(row=row_idx, column=col_id).value
                if id_val is None or id_val == "":
                    continue
                try:
                    irs_id = int(id_val)
                except:
                    atlanan += 1
                    continue

                mevcut_kayit = conn.execute(
                    "SELECT * FROM fason_irsaliye WHERE id = ?", (irs_id,)
                ).fetchone()
                if not mevcut_kayit:
                    atlanan += 1
                    hatalar.append(f"Satır {row_idx}: ID {irs_id} bulunamadı")
                    continue

                degerler = {}
                for kolon_ad, col_idx in eslesenler.items():
                    if col_idx < 0:
                        continue
                    v = ws.cell(row=row_idx, column=col_idx).value
                    if kolon_ad == "irsaliye_tarihi":
                        if v is None or v == "":
                            degerler[kolon_ad] = None
                        elif hasattr(v, "strftime"):
                            degerler[kolon_ad] = v.strftime("%d.%m.%Y")
                        else:
                            degerler[kolon_ad] = str(v).strip()
                    else:
                        if v is None or v == "":
                            degerler[kolon_ad] = None
                        else:
                            try:
                                s = str(v).replace(".", "").replace(",", ".") if isinstance(v, str) else v
                                degerler[kolon_ad] = float(s)
                            except:
                                degerler[kolon_ad] = None

                # ─── Değişiklik tespiti (eski vs yeni) ───
                for alan, yeni_val in degerler.items():
                    eski_val = mevcut_kayit[alan]
                    # Sayısal alanlarda küçük yuvarlama farklarını değişiklik sayma
                    if alan != "irsaliye_tarihi" and eski_val is not None and yeni_val is not None:
                        try:
                            if abs(float(eski_val) - float(yeni_val)) < 0.005:
                                continue
                        except:
                            pass
                    if eski_val != yeni_val:
                        degisiklikler.append({
                            "irsaliye_no": mevcut_kayit["irsaliye_no"],
                            "alan": alan,
                            "alan_etiket": ALAN_ETIKET.get(alan, alan),
                            "eski": eski_val,
                            "yeni": yeni_val,
                            "onemli": alan in ONEMLI_ALANLAR
                        })

                set_parts = []
                vals = []
                for k, val in degerler.items():
                    set_parts.append(f"{k} = ?")
                    vals.append(val)

                if set_parts:
                    vals.append(irs_id)
                    conn.execute(
                        f"UPDATE fason_irsaliye SET {', '.join(set_parts)} WHERE id = ?",
                        vals
                    )
                    guncellenen += 1
            conn.commit()
        finally:
            conn.close()

        # ─── İşlem Geçmişi'ne yaz ───
        log_kaydet(
            "Import Güncelleme",
            f"{dosya.filename}: {guncellenen} kayıt güncellendi" + (f", {atlanan} atlandı" if atlanan else ""),
            None,
            dosya.filename
        )
        # Önemli (OTIS) değişiklikleri ayrıca tek tek logla ki İşlem Geçmişi'nde net görünsün
        for deg in degisiklikler:
            if deg["onemli"]:
                log_kaydet(
                    "Import — Önemli Değişiklik",
                    f"{deg['irsaliye_no']}: {deg['alan_etiket']} {deg['eski']} → {deg['yeni']}",
                    None,
                    deg["irsaliye_no"]
                )

        return jsonify({
            "durum": "ok",
            "guncellenen": guncellenen,
            "atlanan": atlanan,
            "kolonlar": bulunan_kolonlar,
            "hatalar": hatalar[:20],
            "degisiklikler": degisiklikler[:100],
            "mesaj": f"{guncellenen} kayıt güncellendi" + (f", {atlanan} atlandı" if atlanan else "")
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# EXCEL'DEN TOPLU EKLEME — Firma zorunlu (form-data)
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/toplu-ekle", methods=["POST"])
def api_fason_toplu_ekle():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_ekle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    dosya = request.files["dosya"]
    if not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya adı boş"}), 400

    firma_id = request.form.get("firma_id")
    if not firma_id:
        return jsonify({"durum": "hata", "mesaj": "Firma seçiniz"}), 400
    try:
        firma_id = int(firma_id)
    except:
        return jsonify({"durum": "hata", "mesaj": "Geçersiz firma"}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(dosya, data_only=True)
        ws = wb.active

        h_row = None
        for row_idx in range(1, 6):
            row = [str(c.value or "").strip().lower() for c in ws[row_idx]]
            if any("irsaliye" in v or "sipariş" in v or "no" == v.strip() for v in row):
                h_row = row_idx
                break

        veri_baslangic = h_row + 1 if h_row else 1
        col_irs = 1
        col_aciklama = -1

        if h_row:
            basliklar = [str(c.value or "").strip().lower() for c in ws[h_row]]
            for i, b in enumerate(basliklar):
                if "irsaliye" in b or "no" == b.strip() or "sipariş" in b:
                    col_irs = i + 1
                elif "açıklama" in b or "aciklama" in b or "not" in b:
                    col_aciklama = i + 1
                    break

        conn = get_db()
        fv = conn.execute("SELECT id, ad FROM fason_firma WHERE id = ?", (firma_id,)).fetchone()
        if not fv:
            conn.close()
            return jsonify({"durum": "hata", "mesaj": "Firma bulunamadı"}), 400

        eklenen = 0
        mukerrer = 0
        atlanan = 0
        atlanan_sebep = []
        kullanici = session.get("kullanici", "-")

        try:
            for row_idx in range(veri_baslangic, ws.max_row + 1):
                irs_val = ws.cell(row=row_idx, column=col_irs).value
                if irs_val is None:
                    continue
                irs_no = str(irs_val).strip()
                if not irs_no:
                    continue
                if isinstance(irs_val, float) and irs_val.is_integer():
                    irs_no = str(int(irs_val))
                if len(irs_no) > 50:
                    atlanan += 1
                    atlanan_sebep.append(f"Satır {row_idx}: {irs_no[:30]}... (çok uzun)")
                    continue

                aciklama = ""
                if col_aciklama > 0:
                    ac_val = ws.cell(row=row_idx, column=col_aciklama).value
                    if ac_val is not None:
                        aciklama = str(ac_val).strip()[:200]

                var = conn.execute(
                    "SELECT id FROM fason_irsaliye WHERE irsaliye_no = ?",
                    (irs_no,)
                ).fetchone()

                if var:
                    if _fason_yetki_var_mi("fason_mukerrer"):
                        mukerrer += 1
                    else:
                        atlanan += 1
                        atlanan_sebep.append(f"Satır {row_idx}: {irs_no} zaten kayıtlı (mükerrer, atlandı)")
                        continue

                conn.execute("""
                    INSERT INTO fason_irsaliye
                        (irsaliye_no, aciklama, giren_kullanici, firma_id)
                    VALUES (?, ?, ?, ?)
                """, (irs_no, aciklama, kullanici, firma_id))
                eklenen += 1

            conn.commit()
        finally:
            conn.close()

        mesaj = f"{eklenen} irsaliye eklendi"
        if mukerrer:
            mesaj += f" ({mukerrer} tanesi daha önce vardı)"
        if atlanan:
            mesaj += f", {atlanan} satır atlandı"

        log_kaydet(
            "Toplu Ekleme",
            f"{eklenen} irsaliye eklendi — Firma: {fv['ad']}" + (f" ({mukerrer} mükerrer)" if mukerrer else ""),
            None,
            fv["ad"]
        )

        return jsonify({
            "durum": "ok",
            "eklenen": eklenen,
            "mukerrer": mukerrer,
            "atlanan": atlanan,
            "atlanan_sebep": atlanan_sebep[:20],
            "mesaj": mesaj
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# IMPORT GEÇMİŞİ — sevkiyat.db'deki islem_log'dan okur
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/import-log", methods=["GET"])
def api_fason_import_log():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_import_gecmisi"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        conn = sqlite3.connect(SEVKIYAT_DB_YOL, timeout=10)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM islem_log
            WHERE modul = 'Fason' AND islem IN ('Import Güncelleme', 'Import — Önemli Değişiklik')
            ORDER BY id ASC
        """).fetchall()
        conn.close()

        gruplar = []
        aktif = None
        for r in rows:
            if r["islem"] == "Import Güncelleme":
                if aktif:
                    gruplar.append(aktif)
                aktif = {
                    "tarih": r["tarih"],
                    "yapan_ad": r["yapan_ad"],
                    "dosya": r["ilgili_ad"] or "",
                    "ozet": r["detay"] or "",
                    "degisiklikler": []
                }
            else:  # Import — Önemli Değişiklik
                if aktif is None:
                    aktif = {
                        "tarih": r["tarih"], "yapan_ad": r["yapan_ad"],
                        "dosya": "", "ozet": "", "degisiklikler": []
                    }
                aktif["degisiklikler"].append({
                    "irsaliye_no": r["ilgili_ad"] or "",
                    "detay": r["detay"] or ""
                })
        if aktif:
            gruplar.append(aktif)

        gruplar.reverse()  # en yeni en üstte
        return jsonify(gruplar)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500   

# ═════════════════════════════════════════════════
# KAYIT DÜZENLEME (tüm alanlar) — sadece admin rolü
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/duzenle/<int:irs_id>", methods=["POST"])
def api_fason_duzenle(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_duzenle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    try:
        d = request.get_json() or {}
        conn = get_db()
        try:
            eski = conn.execute("SELECT * FROM fason_irsaliye WHERE id = ?", (irs_id,)).fetchone()
            if not eski:
                return jsonify({"durum": "hata", "mesaj": "Bulunamadı"}), 404

            irsaliye_no = (d.get("irsaliye_no") or eski["irsaliye_no"]).strip()
            if not irsaliye_no:
                return jsonify({"durum": "hata", "mesaj": "İrsaliye No zorunlu"}), 400

            firma_id = eski["firma_id"]
            if d.get("firma_id") not in (None, ""):
                try:
                    firma_id = int(d.get("firma_id"))
                    fv = conn.execute("SELECT id FROM fason_firma WHERE id = ?", (firma_id,)).fetchone()
                    if not fv:
                        return jsonify({"durum": "hata", "mesaj": "Firma bulunamadı"}), 400
                except:
                    return jsonify({"durum": "hata", "mesaj": "Geçersiz firma"}), 400

            aciklama = d.get("aciklama", eski["aciklama"] or "")

            def sayi_al(anahtar):
                if anahtar not in d:
                    return eski[anahtar]
                v = d.get(anahtar)
                if v is None or v == "":
                    return None
                try:
                    return float(v)
                except:
                    return eski[anahtar]

            irsaliye_tarihi = d.get("irsaliye_tarihi", eski["irsaliye_tarihi"]) or None
            stok_miktari    = sayi_al("stok_miktari")
            giris_miktari   = sayi_al("giris_miktari")
            otis_stok       = sayi_al("otis_stok")
            otis_giris      = sayi_al("otis_giris")
            toplam_fiyat    = sayi_al("toplam_fiyat")

            conn.execute("""
                UPDATE fason_irsaliye
                SET irsaliye_no = ?, firma_id = ?, aciklama = ?,
                    irsaliye_tarihi = ?, stok_miktari = ?, giris_miktari = ?,
                    otis_stok = ?, otis_giris = ?, toplam_fiyat = ?
                WHERE id = ?
            """, (irsaliye_no, firma_id, aciklama, irsaliye_tarihi,
                  stok_miktari, giris_miktari, otis_stok, otis_giris, toplam_fiyat, irs_id))
            conn.commit()

            alanlar = [
                ("İrsaliye No", eski["irsaliye_no"], irsaliye_no),
                ("Açıklama", eski["aciklama"], aciklama),
                ("İrsaliye Tarihi", eski["irsaliye_tarihi"], irsaliye_tarihi),
                ("Stok Miktarı", eski["stok_miktari"], stok_miktari),
                ("Giriş Miktarı", eski["giris_miktari"], giris_miktari),
                ("OTIS Stok", eski["otis_stok"], otis_stok),
                ("OTIS Giriş", eski["otis_giris"], otis_giris),
                ("Toplam Fiyat", eski["toplam_fiyat"], toplam_fiyat),
            ]
            degisenler = [f"{ad}: {ev if ev is not None else '—'} → {yv if yv is not None else '—'}"
                          for ad, ev, yv in alanlar if ev != yv]
            if eski["firma_id"] != firma_id:
                degisenler.append("Firma değişti")

            log_kaydet(
                "Kayıt Düzenleme",
                f"{irsaliye_no}" + (": " + ", ".join(degisenler) if degisenler else " (değişiklik yok)"),
                irs_id,
                irsaliye_no
            )

            return jsonify({"durum": "ok", "mesaj": "Kayıt güncellendi"})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500 


# ═════════════════════════════════════════════════
# ZMM068 IMPORT — SAP mal girişi raporu
# "Firma Adı" kolonu varsa aynı irsaliye no'lu farklı firmalar ayrı irsaliye olarak tutulur.
# İrsaliye no'ya göre eşleşen satırları kalem olarak işler
# ═════════════════════════════════════════════════

def _firma_norm(s):
    """Firma adı karşılaştırması için: Türkçe büyük/küçük harf ve fazla boşluk duyarsız."""
    s = str(s or "").strip().replace("İ", "i").replace("I", "ı").lower()
    return " ".join(s.split())


@fason_bp.route("/api/fason/zmm068-import", methods=["POST"])
def api_fason_zmm068_import():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    dosya = request.files["dosya"]
    if not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya adı boş"}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(dosya, data_only=True, read_only=True)
        ws = wb.active

        rows_iter = ws.iter_rows(values_only=True)
        try:
            hdr_row = next(rows_iter)
        except StopIteration:
            return jsonify({"durum": "hata", "mesaj": "Dosya boş"}), 400

        hdr = [str(h or "").strip() for h in hdr_row]
        def kol(ad):
            return hdr.index(ad) if ad in hdr else -1

        c_referans   = kol("Referans")
        c_malzeme    = kol("Malzeme")
        c_kisa_metin = kol("Malzeme kısa metni")
        c_miktar     = kol("Miktar (giriş ÖB)")
        c_birim      = kol("Giriş ölçü birimi")
        c_toplam_fyt = kol("Toplam Fiyat")
        c_para       = kol("Para birimi")
        c_birim_fyt  = kol("Birim Fiyat")
        c_belge_tar  = kol("Belge tarihi")
        # Opsiyonel: "Firma Adı" kolonu varsa aynı irsaliye no'lu farklı firma irsaliyeleri ayrıştırılır
        c_firma = next((hdr.index(h) for h in hdr if _firma_norm(h) in ("firma adı", "firma", "firma adi")), -1)

        if c_referans < 0 or c_kisa_metin < 0:
            return jsonify({"durum": "hata", "mesaj": "Beklenen kolonlar bulunamadı (Referans / Malzeme kısa metni)"}), 400

        def al(row, idx):
            if idx < 0 or idx >= len(row):
                return None
            return row[idx]

        def sayi(v):
            if v in (None, ""): return None
            try: return float(v)
            except Exception: return None

        def tarih_str(v):
            if v is None: return None
            if hasattr(v, "strftime"): return v.strftime("%Y-%m-%d")
            return str(v)

        conn = get_db()
        toplam_satir = 0
        eslesen_irsaliye = 0
        yeni_kalem = 0
        guncellenen_kalem = 0
        tasinan_kalem = 0

        # (irsaliye_no, firma_id) -> irsaliye_id cache
        irsaliye_cache = {}
        temizlenen_genel_kalem = set()
        otomatik_olusturulan_irsaliye = set()
        ayristirilan_irsaliye = set()     # aynı no'lu başka firma irsaliyesi vardı → firmaya ayrı irsaliye açıldı
        firma_atanan_irsaliye = set()     # firmasız irsaliyeye dosyadaki firma atandı
        tanimsiz_firmalar = set()         # "Firma Adı" kolonunda olup sistemde bulunmayan firmalar
        takma_no_eslesen = {}             # dosyadaki no → sistemde aynı firmanın farklı numarayla kayıtlı irsaliyesi
        grup_anahtarlari = {}             # (irsaliye_no, firma_id) → dosyadaki kalem anahtarları (SAP kodu / tanım)
        # Bu ÇALIŞMA (import) içinde daha önce işlenen kalemler — aynı dosyada
        # aynı malzemeye ait birden fazla satır varsa (SAP'ın aynı kalemi bölmesi
        # gibi durumlarda) miktarları TOPLAMAK için; farklı bir import çalışmasında
        # (dosya tekrar yüklendiğinde) buradan sıfırlandığı için miktar ikiye katlanmaz.
        bu_calismada_islenen = {}

        # Firma adı → firma_id (büyük/küçük harf ve Türkçe karakter duyarsız)
        firma_harita = {}
        if c_firma >= 0:
            for f in conn.execute("SELECT id, ad FROM fason_firma").fetchall():
                firma_harita[_firma_norm(f["ad"])] = f["id"]

        def firma_id_bul(row):
            if c_firma < 0:
                return None
            ad = str(al(row, c_firma) or "").strip()
            if not ad:
                return None
            fid = firma_harita.get(_firma_norm(ad))
            if fid is None:
                tanimsiz_firmalar.add(ad)
            return fid

        def irsaliye_olustur(irs_no, fid, aciklama):
            return conn.execute("""
                INSERT INTO fason_irsaliye (irsaliye_no, giren_kullanici, aciklama, firma_id)
                VALUES (?, ?, ?, ?)
            """, (irs_no, session.get("kullanici", ""), aciklama, fid)).lastrowid

        def takma_no_irsaliye_bul(irs_no, fid):
            """Bu firmanın bu irsaliyedeki kalemleri, sistemde FARKLI numarayla açılmış bir irsaliyesinde
            zaten duruyor mu? (Örn. mükerrer olmasın diye IRS2026000000003 → KYC2026000000003 diye girilmiş.)
            Kalemlerin en az yarısı (SAP kodu / tanım) aynı firmanın tek bir irsaliyesinde varsa onu döndürür."""
            anahtarlar = grup_anahtarlari.get((irs_no, fid)) or set()
            if not anahtarlar:
                return None
            liste = list(anahtarlar)[:400]
            ph = ",".join("?" * len(liste))
            r = conn.execute(f"""
                SELECT k.irsaliye_id, i.irsaliye_no,
                       COUNT(DISTINCT CASE WHEN k.sap_kodu IN ({ph}) THEN k.sap_kodu ELSE k.malzeme_tanim END) AS ortak
                FROM fason_kalem k
                JOIN fason_irsaliye i ON i.id = k.irsaliye_id
                WHERE i.firma_id = ? AND i.irsaliye_no != ?
                  AND (k.sap_kodu IN ({ph}) OR k.malzeme_tanim IN ({ph}))
                GROUP BY k.irsaliye_id
                ORDER BY ortak DESC, k.irsaliye_id
                LIMIT 1
            """, liste + [fid, irs_no] + liste + liste).fetchone()
            if r and r["ortak"] * 2 >= len(anahtarlar):
                return r
            return None

        def irsaliye_bul(irs_no, fid):
            """Firma biliniyorsa irsaliyeyi (no + firma) ile bulur; aynı no başka firmaya aitse
            o firmanın kalemlerine karışmamak için bu firmaya ayrı bir irsaliye açar."""
            anahtar = (irs_no, fid)
            if anahtar in irsaliye_cache:
                return irsaliye_cache[anahtar]
            adaylar = conn.execute(
                "SELECT id, firma_id FROM fason_irsaliye WHERE irsaliye_no = ? ORDER BY id", (irs_no,)
            ).fetchall()
            if fid is None:
                # Firma bilgisi yok → eski davranış: numarayla ilk kayıt
                if adaylar:
                    irs_id_ = adaylar[0]["id"]
                else:
                    irs_id_ = irsaliye_olustur(irs_no, None, "ZMM068 içe aktarmadan otomatik oluşturuldu — firma ataması gerekiyor")
                    otomatik_olusturulan_irsaliye.add(irs_no)
            else:
                ayni_firma = [a for a in adaylar if a["firma_id"] == fid]
                firmasiz = [a for a in adaylar if a["firma_id"] is None]
                if ayni_firma:
                    irs_id_ = ayni_firma[0]["id"]
                elif firmasiz:
                    irs_id_ = firmasiz[0]["id"]
                    conn.execute("UPDATE fason_irsaliye SET firma_id = ? WHERE id = ?", (fid, irs_id_))
                    firma_atanan_irsaliye.add(irs_no)
                else:
                    takma = takma_no_irsaliye_bul(irs_no, fid)
                    if takma is not None:
                        # Bu firmanın kalemleri başka numarayla girilmiş irsaliyede duruyor → yeni açma, onu kullan
                        irs_id_ = takma["irsaliye_id"]
                        takma_no_eslesen[irs_no] = takma["irsaliye_no"]
                    elif adaylar:
                        irs_id_ = irsaliye_olustur(irs_no, fid, "ZMM068 içe aktarmada aynı numaralı başka firma irsaliyesinden ayrıştırıldı")
                        ayristirilan_irsaliye.add(irs_no)
                    else:
                        irs_id_ = irsaliye_olustur(irs_no, fid, "ZMM068 içe aktarmadan otomatik oluşturuldu")
                        otomatik_olusturulan_irsaliye.add(irs_no)
            irsaliye_cache[anahtar] = irs_id_
            return irs_id_

        try:
            # Satırları önce belleğe al: kalem taşıma kararında "bu kalem dosyada
            # diğer firmaya da ait mi?" sorusunu cevaplayabilmek için.
            satirlar = []
            cikis_satiri_atlanan = 0
            dosyadaki_kalemler = set()   # (irsaliye_no, firma_id, sap_kodu veya tanım)
            for row in rows_iter:
                toplam_satir += 1
                irsaliye_no = str(al(row, c_referans) or "").strip()
                malzeme_tanim = str(al(row, c_kisa_metin) or "").strip()
                if not irsaliye_no or not malzeme_tanim:
                    continue
                # Eksi miktar: giriş ZMM068'inde iptal/iade satırı olabilir (eskisi gibi işlenir).
                # Ama dosyanın TAMAMI eksiyse bu bir çıkış ZMM068'idir — aşağıda reddedilir.
                m_ = sayi(al(row, c_miktar))
                if m_ is not None and m_ < 0:
                    cikis_satiri_atlanan += 1
                fid = firma_id_bul(row)
                sap_ = str(al(row, c_malzeme) or "").strip()
                satirlar.append((row, irsaliye_no, malzeme_tanim, fid))
                dosyadaki_kalemler.add((irsaliye_no, fid, sap_ or malzeme_tanim))
                grup_anahtarlari.setdefault((irsaliye_no, fid), set()).add(sap_ or malzeme_tanim)

            if satirlar and cikis_satiri_atlanan == len(satirlar):
                return jsonify({"durum": "hata", "mesaj":
                    f"Bu dosyadaki {cikis_satiri_atlanan} satırın hepsi eksi miktarlı — bu bir çıkış ZMM068'i. "
                    "Giriş olarak yüklenmedi; 'SAP Çıkış Karşılaştırma' ekranından yükle."}), 400

            for row, irsaliye_no, malzeme_tanim, fid in satirlar:
                irs_id = irsaliye_bul(irsaliye_no, fid)

                # Gerçek malzeme verisi geldiğinde, migration'dan kalan boş "Genel Kalem" plasholder'ını sil
                if irs_id not in temizlenen_genel_kalem:
                    conn.execute("""
                        DELETE FROM fason_kalem
                        WHERE irsaliye_id = ? AND malzeme_tanim = 'Genel Kalem' AND (sap_kodu IS NULL OR sap_kodu = '')
                    """, (irs_id,))
                    temizlenen_genel_kalem.add(irs_id)

                sap_kodu = str(al(row, c_malzeme) or "").strip()
                giris_miktari = sayi(al(row, c_miktar))
                birim = str(al(row, c_birim) or "").strip()
                toplam_fiyat = sayi(al(row, c_toplam_fyt))
                para_birimi = str(al(row, c_para) or "").strip()
                birim_fiyat = sayi(al(row, c_birim_fyt))
                belge_tarihi = tarih_str(al(row, c_belge_tar))

                # Aynı irsaliyede, aynı SAP kodu (varsa) ya da aynı malzeme tanımıyla eşleşen kalem var mı?
                mevcut = None
                if sap_kodu:
                    mevcut = conn.execute(
                        "SELECT id FROM fason_kalem WHERE irsaliye_id = ? AND sap_kodu = ?",
                        (irs_id, sap_kodu)
                    ).fetchone()
                if not mevcut:
                    mevcut = conn.execute(
                        "SELECT id FROM fason_kalem WHERE irsaliye_id = ? AND malzeme_tanim = ?",
                        (irs_id, malzeme_tanim)
                    ).fetchone()

                # Firma biliniyor ama kalem bu irsaliyede yok → daha önceki (firmasız) bir importta
                # aynı numaralı BAŞKA firmanın irsaliyesine yanlışlıkla girmiş olabilir.
                # Öyleyse kalemi durum/fark sebebi/OTIS bilgileriyle birlikte doğru irsaliyeye TAŞI.
                if not mevcut and fid is not None:
                    kalem_anahtari = sap_kodu or malzeme_tanim
                    kosul = "k.sap_kodu = ?" if sap_kodu else "k.malzeme_tanim = ?"
                    kardesler = conn.execute(f"""
                        SELECT k.id, i.firma_id FROM fason_kalem k
                        JOIN fason_irsaliye i ON i.id = k.irsaliye_id
                        WHERE i.irsaliye_no = ? AND k.irsaliye_id != ?
                          AND (i.firma_id IS NULL OR i.firma_id != ?)
                          AND {kosul}
                        ORDER BY k.id
                    """, (irsaliye_no, irs_id, fid, kalem_anahtari)).fetchall()
                    for kd in kardesler:
                        # Kalem dosyada kendi (diğer) firmasına da aitse dokunma — gerçekten iki ayrı kalem
                        if kd["firma_id"] is not None and (irsaliye_no, kd["firma_id"], kalem_anahtari) in dosyadaki_kalemler:
                            continue
                        conn.execute("UPDATE fason_kalem SET irsaliye_id = ? WHERE id = ?", (irs_id, kd["id"]))
                        mevcut = {"id": kd["id"]}
                        tasinan_kalem += 1
                        break

                tekrar_anahtari = (irs_id, sap_kodu or malzeme_tanim)
                bu_dosyada_tekrar = tekrar_anahtari in bu_calismada_islenen

                if mevcut:
                    if bu_dosyada_tekrar:
                        # Aynı dosyada aynı malzemenin ikinci (veya sonraki) satırı — TOPLA
                        onceki = conn.execute(
                            "SELECT giris_miktari, toplam_fiyat FROM fason_kalem WHERE id = ?",
                            (mevcut["id"],)
                        ).fetchone()
                        yeni_miktar = (onceki["giris_miktari"] or 0) + (giris_miktari or 0)
                        yeni_fiyat = (onceki["toplam_fiyat"] or 0) + (toplam_fiyat or 0)
                        conn.execute("""
                            UPDATE fason_kalem
                            SET sap_kodu = ?, giris_miktari = ?, toplam_fiyat = ?,
                                para_birimi = ?, birim_fiyat = ?, belge_tarihi = ?
                            WHERE id = ?
                        """, (sap_kodu, yeni_miktar, yeni_fiyat, para_birimi,
                              birim_fiyat, belge_tarihi, mevcut["id"]))
                    else:
                        # Dosyada ilk kez görülüyor — önceki bir importtan kalma değeri güncelle (üzerine yaz)
                        conn.execute("""
                            UPDATE fason_kalem
                            SET sap_kodu = ?, giris_miktari = ?, toplam_fiyat = ?,
                                para_birimi = ?, birim_fiyat = ?, belge_tarihi = ?
                            WHERE id = ?
                        """, (sap_kodu, giris_miktari, toplam_fiyat, para_birimi,
                              birim_fiyat, belge_tarihi, mevcut["id"]))
                    guncellenen_kalem += 1
                else:
                    conn.execute("""
                        INSERT INTO fason_kalem
                            (irsaliye_id, malzeme_tanim, sap_kodu, giris_miktari,
                             toplam_fiyat, para_birimi, birim_fiyat, belge_tarihi, durum)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')
                    """, (irs_id, malzeme_tanim, sap_kodu, giris_miktari,
                          toplam_fiyat, para_birimi, birim_fiyat, belge_tarihi))
                    yeni_kalem += 1

                bu_calismada_islenen[tekrar_anahtari] = True

            eslesen_irsaliye = len(set(irsaliye_cache.values()))
            # Tarihi boş irsaliyelere ZMM068 belge tarihini yaz (dolu tarihlere dokunmaz)
            tarih_doldurulan = _irsaliye_tarihi_doldur(conn, set(irsaliye_cache.values()))
            conn.commit()
        finally:
            conn.close()

        firma_kolonu_var = c_firma >= 0
        log_kaydet(
            "ZMM068 Import",
            f"{dosya.filename}: {yeni_kalem} yeni kalem, {guncellenen_kalem} güncellendi, "
            f"{eslesen_irsaliye} irsaliye eşleşti, {len(otomatik_olusturulan_irsaliye)} irsaliye otomatik oluşturuldu"
            + (f", {len(ayristirilan_irsaliye)} mükerrer no firmaya göre ayrıştırıldı ({', '.join(sorted(ayristirilan_irsaliye)[:10])})" if ayristirilan_irsaliye else "")
            + (f", farklı numarayla eşleşen: {', '.join(f'{k}→{v}' for k, v in sorted(takma_no_eslesen.items()))}" if takma_no_eslesen else "")
            + (f", {tasinan_kalem} kalem doğru firmanın irsaliyesine taşındı" if tasinan_kalem else "")
            + (f", {len(firma_atanan_irsaliye)} firmasız irsaliyeye firma atandı" if firma_atanan_irsaliye else "")
            + (f", tanınmayan firma: {', '.join(sorted(tanimsiz_firmalar))}" if tanimsiz_firmalar else ""),
            None, dosya.filename
        )

        mesaj = f"{yeni_kalem} yeni kalem, {guncellenen_kalem} güncellendi"
        if tarih_doldurulan:
            mesaj += f" · {tarih_doldurulan} irsaliyenin boş tarihi belge tarihinden dolduruldu"
        if ayristirilan_irsaliye:
            mesaj += f" · {len(ayristirilan_irsaliye)} mükerrer irsaliye no firmaya göre ayrıldı ({', '.join(sorted(ayristirilan_irsaliye)[:5])})"
        if takma_no_eslesen:
            mesaj += " · Farklı numarayla kayıtlı irsaliyelerle eşleşti: " + ", ".join(
                f"{k} → {v}" for k, v in sorted(takma_no_eslesen.items())[:5])
        if tasinan_kalem:
            mesaj += f" · {tasinan_kalem} kalem doğru firmanın irsaliyesine taşındı (durumları korundu)"
        if firma_atanan_irsaliye:
            mesaj += f" · {len(firma_atanan_irsaliye)} firmasız irsaliyeye firma atandı"
        if otomatik_olusturulan_irsaliye:
            mesaj += (f" · {len(otomatik_olusturulan_irsaliye)} irsaliye sistemde yoktu, otomatik oluşturuldu"
                      + ("" if firma_kolonu_var else " — firma ataması yapman gerekiyor"))
        if tanimsiz_firmalar:
            mesaj += f" · Sistemde olmayan firma adı (firmasız işlendi): {', '.join(sorted(tanimsiz_firmalar)[:5])}"

        return jsonify({
            "durum": "ok",
            "toplam_satir": toplam_satir,
            "eslesen_irsaliye": eslesen_irsaliye,
            "otomatik_olusturulan_irsaliye": sorted(otomatik_olusturulan_irsaliye)[:30],
            "ayristirilan_irsaliye": sorted(ayristirilan_irsaliye)[:30],
            "tasinan_kalem": tasinan_kalem,
            "takma_no_eslesen": [{"dosyadaki": k, "sistemdeki": v} for k, v in sorted(takma_no_eslesen.items())][:30],
            "firma_atanan_irsaliye": sorted(firma_atanan_irsaliye)[:30],
            "tanimsiz_firmalar": sorted(tanimsiz_firmalar),
            "firma_kolonu_var": firma_kolonu_var,
            "yeni_kalem": yeni_kalem,
            "guncellenen_kalem": guncellenen_kalem,
            "mesaj": mesaj
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

# ═════════════════════════════════════════════════
# OTIS IMPORT — "Tüm Gelen Malzemeler" export'u
# Kalemleri OTIS Stok/OTIS Giriş ile eşleştirir
# ═════════════════════════════════════════════════

BENZERLIK_ESIGI = 0.98


@fason_bp.route("/api/fason/otis-import", methods=["POST"])
def api_fason_otis_import():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    dosya = request.files["dosya"]
    if not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya adı boş"}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(dosya, data_only=True, read_only=True)
        ws = wb.active

        rows_iter = ws.iter_rows(values_only=True)
        try:
            hdr_row = next(rows_iter)
        except StopIteration:
            return jsonify({"durum": "hata", "mesaj": "Dosya boş"}), 400

        hdr = [str(h or "").strip().upper() for h in hdr_row]
        def kol(*adaylar):
            for a in adaylar:
                if a in hdr:
                    return hdr.index(a)
            return -1

        c_stok      = kol("STOK")
        c_irs_no    = kol("İRSALİYE NO", "IRSALIYE NO")
        c_mal_grubu = kol("MAL GRUBU")
        c_tanim     = kol("MALZEME TANIM")
        c_birim     = kol("BİRİM", "BIRIM")
        c_sap       = kol("SAP KODU")
        c_mkg       = kol("MİKTAR(KG)", "MIKTAR(KG)")
        c_madt      = kol("MİKTAR(ADT)", "MIKTAR(ADT)")

        if c_irs_no < 0 or c_tanim < 0:
            return jsonify({"durum": "hata", "mesaj": "Beklenen kolonlar bulunamadı (İRSALİYE NO / MALZEME TANIM)"}), 400

        def al(row, idx):
            if idx < 0 or idx >= len(row):
                return None
            return row[idx]

        def sayi(v):
            if v is None or v == "":
                return None
            try:
                return float(v)
            except Exception:
                try:
                    return float(str(v).replace(",", "."))
                except Exception:
                    return None

        def giris_miktari_hesapla(birim, mkg, madt):
            b = (birim or "").strip().upper()
            if b == "KG":
                return sayi(mkg)
            m = sayi(madt)
            return m if m is not None else sayi(mkg)

        conn = get_db()
        toplam_satir = 0
        eslesmeyen_irsaliye = 0
        dogrudan_guncellenen = 0
        otomatik_eslesen = 0
        beklemeye_alinan = 0
        atlanan_irsaliyeler = []
        stok_degisen_kalemler = []

        def stok_karsilastir_ve_isle(kalem_row, yeni_stok, irs_no_):
            eski = kalem_row["otis_stok"]
            if eski != yeni_stok and (eski is not None or yeni_stok is not None):
                stok_degisen_kalemler.append({
                    "irsaliye_no": irs_no_,
                    "malzeme_tanim": kalem_row["malzeme_tanim"],
                    "eski_stok": eski,
                    "yeni_stok": yeni_stok
                })
                return eski
            return None

        irsaliye_cache = {}       # irsaliye_no -> [irs_id, ...] (boş liste ise sistemde yok)
        kalem_cache = {}          # irsaliye_no -> [o numaradaki tüm irsaliyelerin fason_kalem satırları]
        temizlenen_genel_kalem = set()

        try:
            # 1) ÖNCE: aynı irsaliyede aynı malzeme tanımıyla gelen satırları TOPLA
            gruplu = {}
            grup_sira = []
            for row in rows_iter:
                toplam_satir += 1
                irsaliye_no = str(al(row, c_irs_no) or "").strip()
                malzeme_tanim = str(al(row, c_tanim) or "").strip()
                if not irsaliye_no or not malzeme_tanim:
                    continue

                birim = str(al(row, c_birim) or "").strip()
                otis_giris_deger = giris_miktari_hesapla(birim, al(row, c_mkg), al(row, c_madt))
                otis_stok_deger = sayi(al(row, c_stok))
                sap_kodu_deger = str(al(row, c_sap) or "").strip()
                mal_grubu_deger = str(al(row, c_mal_grubu) or "").strip()

                anahtar = (irsaliye_no, malzeme_tanim)
                if anahtar not in gruplu:
                    gruplu[anahtar] = {
                        "irsaliye_no": irsaliye_no,
                        "malzeme_tanim": malzeme_tanim,
                        "otis_stok": None,
                        "otis_giris": None,
                        "sap_kodu": sap_kodu_deger,
                        "mal_grubu": mal_grubu_deger,
                    }
                    grup_sira.append(anahtar)

                g = gruplu[anahtar]
                if otis_stok_deger is not None:
                    g["otis_stok"] = (g["otis_stok"] or 0) + otis_stok_deger
                if otis_giris_deger is not None:
                    g["otis_giris"] = (g["otis_giris"] or 0) + otis_giris_deger
                if sap_kodu_deger and not g["sap_kodu"]:
                    g["sap_kodu"] = sap_kodu_deger
                if mal_grubu_deger and not g["mal_grubu"]:
                    g["mal_grubu"] = mal_grubu_deger

            # 2) SONRA: toplanmış her (irsaliye, malzeme) grubunu tek kalem gibi işle
            for anahtar in grup_sira:
                g = gruplu[anahtar]
                irsaliye_no = g["irsaliye_no"]
                malzeme_tanim = g["malzeme_tanim"]
                otis_stok = g["otis_stok"]
                otis_giris = g["otis_giris"]
                sap_kodu_otis = g["sap_kodu"]
                mal_grubu = g["mal_grubu"]

                # Aynı numaralı birden fazla irsaliye olabilir (farklı firmalar — mükerrer no).
                # OTIS export'unda firma yok; bu yüzden o numaradaki TÜM irsaliyelerin kalemleri
                # arasında eşleştirme yapılır, her kalem kendi irsaliyesinde güncellenir.
                if irsaliye_no not in irsaliye_cache:
                    irsaliye_cache[irsaliye_no] = [r["id"] for r in conn.execute(
                        "SELECT id FROM fason_irsaliye WHERE irsaliye_no = ? ORDER BY id", (irsaliye_no,)
                    ).fetchall()]

                irs_idler = irsaliye_cache[irsaliye_no]
                if not irs_idler:
                    eslesmeyen_irsaliye += 1
                    atlanan_irsaliyeler.append(irsaliye_no)
                    continue
                irs_id = irs_idler[0]   # eşleşmeyen satır bekleme listesine bu irsaliye altında düşer

                # Gerçek malzeme verisi geldiğinde, migration'dan kalan boş "Genel Kalem" plasholder'ını sil
                for _iid in irs_idler:
                    if _iid not in temizlenen_genel_kalem:
                        conn.execute("""
                            DELETE FROM fason_kalem
                            WHERE irsaliye_id = ? AND malzeme_tanim = 'Genel Kalem' AND (sap_kodu IS NULL OR sap_kodu = '')
                        """, (_iid,))
                        temizlenen_genel_kalem.add(_iid)

                if irsaliye_no not in kalem_cache:
                    ph = ",".join("?" * len(irs_idler))
                    kalem_cache[irsaliye_no] = conn.execute(
                        f"SELECT * FROM fason_kalem WHERE irsaliye_id IN ({ph})", irs_idler
                    ).fetchall()

                kalemler = kalem_cache[irsaliye_no]

                # 1) DAHA ÖNCE OTIS'E BAĞLANMIŞ KALEM VAR MI? (doğrudan güncelle)
                dogrudan = next((k for k in kalemler if k["otis_malzeme_tanim"] == malzeme_tanim), None)
                if dogrudan:
                    eski_stok = stok_karsilastir_ve_isle(dogrudan, otis_stok, irsaliye_no)
                    conn.execute(
                        "UPDATE fason_kalem SET otis_stok = ?, otis_giris = ?, otis_stok_onceki = ?, otis_stok_son_degisim_tarihi = ? WHERE id = ?",
                        (otis_stok, otis_giris, eski_stok,
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S") if eski_stok is not None else dogrudan["otis_stok_son_degisim_tarihi"],
                         dogrudan["id"])
                    )
                    dogrudan_guncellenen += 1
                    continue

                # 2) SAP KODU İLE OTOMATİK EŞLEŞTİRME (henüz OTIS'e bağlanmamış kalemler arasında)
                aday = None
                eslesme_tipi = None
                if sap_kodu_otis:
                    sap_adaylari = [k for k in kalemler if k["otis_malzeme_tanim"] is None and k["sap_kodu"] == sap_kodu_otis]
                    if len(sap_adaylari) == 1:
                        aday = sap_adaylari[0]
                        eslesme_tipi = "sap"

                # 3) %98+ METİN BENZERLİĞİ İLE OTOMATİK EŞLEŞTİRME
                if aday is None:
                    eslesme_tipi = "benzerlik"
                    benzer_adaylar = []
                    for k in kalemler:
                        if k["otis_malzeme_tanim"] is not None:
                            continue
                        oran = _benzerlik_orani(malzeme_tanim, k["malzeme_tanim"])
                        if oran >= BENZERLIK_ESIGI:
                            benzer_adaylar.append(k)
                    if len(benzer_adaylar) == 1:
                        aday = benzer_adaylar[0]

                if aday:
                    eski_stok = stok_karsilastir_ve_isle(aday, otis_stok, irsaliye_no)
                    conn.execute("""
                        UPDATE fason_kalem
                        SET otis_malzeme_tanim = ?, otis_sap_kodu = ?, otis_stok = ?, otis_giris = ?,
                            otis_stok_onceki = ?, otis_stok_son_degisim_tarihi = ?,
                            otis_eslesme_tipi = ?, otis_eslesme_tarihi = ?, otis_eslesen_kullanici = ?
                        WHERE id = ?
                    """, (malzeme_tanim, sap_kodu_otis, otis_stok, otis_giris, eski_stok,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S") if eski_stok is not None else aday["otis_stok_son_degisim_tarihi"],
                          eslesme_tipi, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), session.get("kullanici", ""),
                          aday["id"]))
                    otomatik_eslesen += 1
                    # cache'i güncelle ki aynı import içinde tekrar eşleşmesin
                    kalem_cache[irsaliye_no] = [
                        dict(k, otis_malzeme_tanim=malzeme_tanim) if k["id"] == aday["id"] else k
                        for k in kalemler
                    ]
                    continue

                # 4) HİÇBİR ADAY YOK/BELİRSİZ → BEKLEME LİSTESİNE AL
                var_mi = conn.execute("""
                    SELECT id FROM fason_otis_bekleyen
                    WHERE irsaliye_id = ? AND malzeme_tanim = ?
                """, (irs_id, malzeme_tanim)).fetchone()
                if var_mi:
                    conn.execute(
                        "UPDATE fason_otis_bekleyen SET otis_stok = ?, otis_giris = ?, sap_kodu = ?, mal_grubu = ? WHERE id = ?",
                        (otis_stok, otis_giris, sap_kodu_otis, mal_grubu, var_mi["id"])
                    )
                else:
                    conn.execute("""
                        INSERT INTO fason_otis_bekleyen
                            (irsaliye_id, malzeme_tanim, mal_grubu, sap_kodu, otis_stok, otis_giris)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (irs_id, malzeme_tanim, mal_grubu, sap_kodu_otis, otis_stok, otis_giris))
                    beklemeye_alinan += 1

            conn.commit()
        finally:
            conn.close()

        log_kaydet(
            "OTIS Import",
            f"{dosya.filename}: {dogrudan_guncellenen} güncellendi, {otomatik_eslesen} otomatik eşleşti, "
            f"{beklemeye_alinan} eşleştirme bekliyor, {eslesmeyen_irsaliye} satır sistemde olmayan irsaliyeye ait",
            None, dosya.filename
        )

        return jsonify({
            "durum": "ok",
            "toplam_satir": toplam_satir,
            "dogrudan_guncellenen": dogrudan_guncellenen,
            "otomatik_eslesen": otomatik_eslesen,
            "beklemeye_alinan": beklemeye_alinan,
            "eslesmeyen_satir": eslesmeyen_irsaliye,
            "atlanan_irsaliyeler": sorted(set(atlanan_irsaliyeler))[:30],
            "stok_degisen_kalemler": stok_degisen_kalemler[:200],
            "stok_degisen_sayisi": len(stok_degisen_kalemler),
            "mesaj": f"{dogrudan_guncellenen + otomatik_eslesen} kalem güncellendi, {beklemeye_alinan} kalem elle eşleştirme bekliyor, {len(stok_degisen_kalemler)} kalemde stok değişti"
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

# ═════════════════════════════════════════════════
# ELLE EŞLEŞTİRME — bekleyen OTIS satırlarını çözme
# ═════════════════════════════════════════════════

@fason_bp.route("/api/fason/otis-bekleyen", methods=["GET"])
def api_fason_otis_bekleyen():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        bekleyenler = conn.execute("""
            SELECT b.*, i.irsaliye_no
            FROM fason_otis_bekleyen b
            JOIN fason_irsaliye i ON i.id = b.irsaliye_id
            ORDER BY i.irsaliye_no, b.id
        """).fetchall()

        sonuc = []
        for b in bekleyenler:
            adaylar = conn.execute("""
                SELECT id, malzeme_tanim, sap_kodu, stok_miktari, giris_miktari
                FROM fason_kalem
                WHERE irsaliye_id = ? AND otis_malzeme_tanim IS NULL
            """, (b["irsaliye_id"],)).fetchall()
            d = dict(b)
            d["adaylar"] = [dict(a) for a in adaylar]
            # SAP (ZMM068) kaynaklı gerçek kalem var mı? ("Genel Kalem" / sap_kodu boş olanlar sayılmaz)
            d["sap_kalemi_var_mi"] = any((a["sap_kodu"] or "").strip() for a in adaylar)
            sonuc.append(d)
        return jsonify(sonuc)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-eslestir", methods=["POST"])
def api_fason_otis_eslestir():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    d = request.get_json() or {}
    bekleyen_id = d.get("bekleyen_id")
    kalem_id = d.get("kalem_id")
    if not bekleyen_id or not kalem_id:
        return jsonify({"durum": "hata", "mesaj": "bekleyen_id ve kalem_id zorunlu"}), 400

    conn = get_db()
    try:
        b = conn.execute("SELECT * FROM fason_otis_bekleyen WHERE id = ?", (bekleyen_id,)).fetchone()
        if not b:
            return jsonify({"durum": "hata", "mesaj": "Bekleyen kayıt bulunamadı"}), 404
        k = conn.execute("SELECT * FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
        if not k:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404

        if k["otis_malzeme_tanim"]:
            return jsonify({"durum": "hata", "mesaj": "Bu kalem zaten bir OTIS kaydıyla eşleşmiş — 'OTIS Eşleşmeleri' ekranından değiştir"}), 409

        conn.execute("""
            UPDATE fason_kalem
            SET otis_malzeme_tanim = ?, otis_sap_kodu = ?, otis_stok = ?, otis_giris = ?,
                otis_eslesme_tipi = 'elle', otis_eslesme_tarihi = ?, otis_eslesen_kullanici = ?
            WHERE id = ?
        """, (b["malzeme_tanim"], b["sap_kodu"], b["otis_stok"], b["otis_giris"],
              _simdi(), session.get("kullanici", ""), kalem_id))
        conn.execute("DELETE FROM fason_otis_bekleyen WHERE id = ?", (bekleyen_id,))
        conn.commit()

        log_kaydet("OTIS Elle Eşleştirme", f"{b['malzeme_tanim']} → {k['malzeme_tanim']}", kalem_id, k["malzeme_tanim"])
        return jsonify({"durum": "ok", "mesaj": "Eşleştirildi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-yeni-kalem/<int:bekleyen_id>", methods=["POST"])
def api_fason_otis_yeni_kalem(bekleyen_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        b = conn.execute("SELECT * FROM fason_otis_bekleyen WHERE id = ?", (bekleyen_id,)).fetchone()
        if not b:
            return jsonify({"durum": "hata", "mesaj": "Bekleyen kayıt bulunamadı"}), 404
        
        cur = conn.execute("""
            INSERT INTO fason_kalem
                (irsaliye_id, malzeme_tanim, otis_malzeme_tanim, otis_sap_kodu, otis_stok, otis_giris, durum,
                 otis_eslesme_tipi, otis_eslesme_tarihi, otis_eslesen_kullanici)
            VALUES (?, ?, ?, ?, ?, ?, '', 'yeni_kalem', ?, ?)
        """, (b["irsaliye_id"], b["malzeme_tanim"], b["malzeme_tanim"], b["sap_kodu"], b["otis_stok"], b["otis_giris"],
              _simdi(), session.get("kullanici", "")))
        conn.execute("DELETE FROM fason_otis_bekleyen WHERE id = ?", (bekleyen_id,))
        conn.commit()

        log_kaydet("OTIS Yeni Kalem", b["malzeme_tanim"], cur.lastrowid, b["malzeme_tanim"])
        return jsonify({"durum": "ok", "id": cur.lastrowid, "mesaj": "Yeni kalem oluşturuldu"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-bekleyen-sil/<int:bekleyen_id>", methods=["DELETE"])
def api_fason_otis_bekleyen_sil(bekleyen_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        conn.execute("DELETE FROM fason_otis_bekleyen WHERE id = ?", (bekleyen_id,))
        conn.commit()
        return jsonify({"durum": "ok", "mesaj": "Silindi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()

# ═════════════════════════════════════════════════
# OTIS EŞLEŞMELERİ — yapılmış eşleşmeleri kontrol et / değiştir / kaldır
# ═════════════════════════════════════════════════

# "Şüpheli" eşleşme: tanımlar farklı VE SAP koduyla da teyit edilmemiş — yanlış eşleşme riski
# asıl burada. (Tanım birebir aynı ama OTIS'teki SAP kodu farklı olanlar ayrı "sap_farkli"
# sekmesinde; bunlar genelde OTIS tarafında eski/farklı kod girilmesinden kaynaklanır.)
_SUPHELI_SQL = """(
    k.malzeme_tanim <> k.otis_malzeme_tanim
    AND NOT (COALESCE(k.sap_kodu,'') <> '' AND k.sap_kodu = COALESCE(k.otis_sap_kodu,''))
)"""
_SAP_FARKLI_SQL = """(
    k.malzeme_tanim = k.otis_malzeme_tanim
    AND COALESCE(k.sap_kodu,'') <> '' AND COALESCE(k.otis_sap_kodu,'') <> '' AND k.sap_kodu <> k.otis_sap_kodu
)"""

_ESLESME_TIP_SQL = {
    "elle": "k.otis_eslesme_tipi = 'elle'",
    "otomatik": "k.otis_eslesme_tipi IN ('sap', 'benzerlik')",
    "yeni_kalem": "k.otis_eslesme_tipi = 'yeni_kalem'",
    "eski": "k.otis_eslesme_tipi IS NULL",
    "supheli": _SUPHELI_SQL,
    "sap_farkli": _SAP_FARKLI_SQL,
}


def _otis_sadece_kalem_mi(k):
    """OTIS'ten 'Yeni Kalem' ile açılmış, SAP tarafı olmayan kalem mi?"""
    return (k["otis_eslesme_tipi"] == "yeni_kalem"
            or (not (k["sap_kodu"] or "").strip() and k["giris_miktari"] is None
                and (k["malzeme_tanim"] or "") == (k["otis_malzeme_tanim"] or "")))


@fason_bp.route("/api/fason/otis-eslesmeler", methods=["GET"])
def api_fason_otis_eslesmeler():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    ara = request.args.get("ara", "").strip()
    filtre = request.args.get("filtre", "").strip()
    irsaliye_no = request.args.get("irsaliye_no", "").strip()
    try:
        limit = max(1, min(1000, int(request.args.get("limit", 300))))
    except ValueError:
        limit = 300

    conn = get_db()
    try:
        temel = """
            FROM fason_kalem k
            JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            LEFT JOIN fason_firma f ON f.id = i.firma_id
            WHERE k.otis_malzeme_tanim IS NOT NULL
        """
        params = []
        if irsaliye_no:
            temel += " AND i.irsaliye_no = ?"
            params.append(irsaliye_no)
        if ara:
            temel += """ AND (i.irsaliye_no LIKE ? OR k.malzeme_tanim LIKE ? OR k.otis_malzeme_tanim LIKE ?
                              OR k.sap_kodu LIKE ? OR k.otis_sap_kodu LIKE ? OR f.ad LIKE ?)"""
            params += [f"%{ara}%"] * 6

        # Sekme sayıları (arama/irsaliye filtresi uygulanmış haliyle)
        sayilar = {"tumu": conn.execute("SELECT COUNT(*) " + temel, params).fetchone()[0]}
        for ad, kosul in _ESLESME_TIP_SQL.items():
            sayilar[ad] = conn.execute(f"SELECT COUNT(*) {temel} AND {kosul}", params).fetchone()[0]

        sorgu = """
            SELECT k.id, k.irsaliye_id, i.irsaliye_no, COALESCE(f.ad, '') AS firma_ad,
                   k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.stok_miktari, k.durum,
                   k.otis_malzeme_tanim, k.otis_sap_kodu, k.otis_giris, k.otis_stok,
                   k.otis_eslesme_tipi, k.otis_eslesme_tarihi, k.otis_eslesen_kullanici
        """ + temel
        if filtre in _ESLESME_TIP_SQL:
            sorgu += f" AND {_ESLESME_TIP_SQL[filtre]}"
        sorgu += " ORDER BY COALESCE(k.otis_eslesme_tarihi, '') DESC, k.id DESC LIMIT ?"
        rows = conn.execute(sorgu, params + [limit]).fetchall()

        kalemler = []
        for r in rows:
            d = dict(r)
            d["benzerlik"] = round(_benzerlik_orani(r["malzeme_tanim"], r["otis_malzeme_tanim"]) * 100)
            sap, osap = (r["sap_kodu"] or "").strip(), (r["otis_sap_kodu"] or "").strip()
            d["sap_uyumu"] = "ayni" if sap and osap and sap == osap else ("farkli" if sap and osap else "bilinmiyor")
            d["supheli"] = (r["malzeme_tanim"] or "") != (r["otis_malzeme_tanim"] or "") and d["sap_uyumu"] != "ayni"
            d["sadece_otis"] = _otis_sadece_kalem_mi(r)
            kalemler.append(d)
        return jsonify({"sayilar": sayilar, "gosterilen": len(kalemler), "kalemler": kalemler})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-eslesme-adaylar/<int:kalem_id>", methods=["GET"])
def api_fason_otis_eslesme_adaylar(kalem_id):
    """Bu kalemin OTIS kaydının taşınabileceği kalemler: aynı irsaliye no'daki (mükerrer no'lu
    diğer firmalar dahil) diğer kalemler. Zaten eşleşmiş olanlar seçilirse iki kalemin OTIS'i yer değiştirir."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        k = conn.execute("""
            SELECT k.*, i.irsaliye_no FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE k.id = ?
        """, (kalem_id,)).fetchone()
        if not k:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404
        rows = conn.execute("""
            SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.otis_malzeme_tanim, COALESCE(f.ad, '') AS firma_ad
            FROM fason_kalem k
            JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            LEFT JOIN fason_firma f ON f.id = i.firma_id
            WHERE i.irsaliye_no = ? AND k.id != ?
        """, (k["irsaliye_no"], kalem_id)).fetchall()
        hedef_tanim = k["otis_malzeme_tanim"] or ""
        adaylar = []
        for r in rows:
            d = dict(r)
            d["benzerlik"] = round(_benzerlik_orani(hedef_tanim, r["malzeme_tanim"]) * 100)
            d["sap_ayni"] = bool((r["sap_kodu"] or "").strip()) and (r["sap_kodu"] or "").strip() == (k["otis_sap_kodu"] or "").strip()
            adaylar.append(d)
        # Önce SAP kodu tutanlar, sonra benzerliğe göre; eşleşmemişler eşleşmişlerden önce
        adaylar.sort(key=lambda a: (not a["sap_ayni"], a["otis_malzeme_tanim"] is not None, -a["benzerlik"]))
        return jsonify({"adaylar": adaylar})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-eslesme-degistir", methods=["POST"])
def api_fason_otis_eslesme_degistir():
    """kalem_id'deki OTIS kaydını hedef_kalem_id'ye taşır. Hedef zaten eşleşmişse iki kalemin
    OTIS bilgileri yer değiştirir (takas). Kaynak OTIS'ten açılmış bir 'yeni kalem' ise
    (SAP tarafı yok) boş kalmasın diye silinir; durumu varsa ve hedefte durum yoksa hedefe aktarılır."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}
    try:
        kalem_id = int(d.get("kalem_id"))
        hedef_id = int(d.get("hedef_kalem_id"))
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "kalem_id ve hedef_kalem_id zorunlu"}), 400
    if kalem_id == hedef_id:
        return jsonify({"durum": "hata", "mesaj": "Aynı kalem seçildi"}), 400

    otis_alanlar = ["otis_malzeme_tanim", "otis_sap_kodu", "otis_stok", "otis_giris",
                    "otis_stok_onceki", "otis_stok_son_degisim_tarihi"]
    conn = get_db()
    try:
        q = "SELECT k.*, i.irsaliye_no FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE k.id = ?"
        kaynak = conn.execute(q, (kalem_id,)).fetchone()
        hedef = conn.execute(q, (hedef_id,)).fetchone()
        if not kaynak or not hedef:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404
        if not kaynak["otis_malzeme_tanim"]:
            return jsonify({"durum": "hata", "mesaj": "Kaynak kalemin OTIS eşleşmesi yok"}), 400
        if kaynak["irsaliye_no"] != hedef["irsaliye_no"]:
            return jsonify({"durum": "hata", "mesaj": "Sadece aynı irsaliye no'daki kalemler arasında değiştirilebilir"}), 400

        simdi, kullanici = _simdi(), session.get("kullanici", "")
        set_sql = ", ".join(f"{a} = ?" for a in otis_alanlar) + \
            ", otis_eslesme_tipi = ?, otis_eslesme_tarihi = ?, otis_eslesen_kullanici = ?"
        kaynak_otis = [kaynak[a] for a in otis_alanlar]
        hedef_otis = [hedef[a] for a in otis_alanlar]
        takas = bool(hedef["otis_malzeme_tanim"])
        kaynak_sadece_otis = _otis_sadece_kalem_mi(kaynak)

        if takas and kaynak_sadece_otis:
            return jsonify({"durum": "hata", "mesaj": "Bu kalem OTIS'ten açılmış (SAP tarafı yok); zaten eşleşmiş bir kalemle takas edilemez. Önce hedefin eşleşmesini kaldır."}), 400

        # Hedefe kaynağın OTIS'i
        conn.execute(f"UPDATE fason_kalem SET {set_sql} WHERE id = ?",
                     kaynak_otis + ["elle", simdi, kullanici, hedef_id])
        mesaj = ""
        if takas:
            conn.execute(f"UPDATE fason_kalem SET {set_sql} WHERE id = ?",
                         hedef_otis + ["elle", simdi, kullanici, kalem_id])
            mesaj = "İki kalemin OTIS eşleşmesi yer değiştirdi"
            log_kaydet("OTIS Eşleşme Takas",
                       f"{kaynak['malzeme_tanim']} ⇄ {hedef['malzeme_tanim']}", kalem_id, kaynak["irsaliye_no"])
        elif kaynak_sadece_otis:
            # OTIS'ten açılmış kalem — SAP kalemine bağlandı, boş kopyası kalmasın
            if (kaynak["durum"] or "") and not (hedef["durum"] or ""):
                conn.execute("UPDATE fason_kalem SET durum = ?, durum_tarihi = ? WHERE id = ?",
                             (kaynak["durum"], kaynak["durum_tarihi"], hedef_id))
            # Çıkış irsaliyeleri fiziksel malzemeye ait — hedef kaleme taşı (hedefte aynı no varsa tekrar ekleme)
            for c in conn.execute("SELECT * FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,)).fetchall():
                _cikis_ekle(conn, hedef_id, c["irsaliye_no"], c["miktar"], c["tarih"], c["kaynak"] or "elle", c["ekleyen"] or "",
                            c["tip"] or "cikis")
            conn.execute("DELETE FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,))
            _cikis_ozet_guncelle(conn, hedef_id)
            conn.execute("DELETE FROM fason_kalem WHERE id = ?", (kalem_id,))
            mesaj = "OTIS kaydı SAP kalemine bağlandı, OTIS'ten açılan geçici kalem silindi"
            log_kaydet("OTIS Eşleşme Değiştir",
                       f"{kaynak['otis_malzeme_tanim']} → {hedef['malzeme_tanim']} (OTIS'ten açılan kalem silindi)",
                       hedef_id, kaynak["irsaliye_no"])
        else:
            conn.execute(f"UPDATE fason_kalem SET {set_sql} WHERE id = ?",
                         [None] * len(otis_alanlar) + [None, None, None, kalem_id])
            mesaj = "OTIS eşleşmesi diğer kaleme taşındı"
            log_kaydet("OTIS Eşleşme Değiştir",
                       f"{kaynak['otis_malzeme_tanim']}: {kaynak['malzeme_tanim']} → {hedef['malzeme_tanim']}",
                       hedef_id, kaynak["irsaliye_no"])
        conn.commit()
        return jsonify({"durum": "ok", "mesaj": mesaj, "takas": takas})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-eslesme-kaldir", methods=["POST"])
def api_fason_otis_eslesme_kaldir():
    """Eşleşmeyi geri alır: OTIS kaydı 'Eşleştirme Bekleyenler'e geri döner, kalemin OTIS alanları
    boşalır. OTIS'ten açılmış kalemse (SAP tarafı yok) kalem silinir."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}
    try:
        kalem_id = int(d.get("kalem_id"))
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "kalem_id zorunlu"}), 400

    conn = get_db()
    try:
        k = conn.execute("""
            SELECT k.*, i.irsaliye_no FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE k.id = ?
        """, (kalem_id,)).fetchone()
        if not k:
            return jsonify({"durum": "hata", "mesaj": "Kalem bulunamadı"}), 404
        if not k["otis_malzeme_tanim"]:
            return jsonify({"durum": "hata", "mesaj": "Bu kalemin OTIS eşleşmesi yok"}), 400

        var_mi = conn.execute(
            "SELECT id FROM fason_otis_bekleyen WHERE irsaliye_id = ? AND malzeme_tanim = ?",
            (k["irsaliye_id"], k["otis_malzeme_tanim"])
        ).fetchone()
        if var_mi:
            conn.execute("UPDATE fason_otis_bekleyen SET otis_stok = ?, otis_giris = ?, sap_kodu = ? WHERE id = ?",
                         (k["otis_stok"], k["otis_giris"], k["otis_sap_kodu"] or "", var_mi["id"]))
        else:
            conn.execute("""
                INSERT INTO fason_otis_bekleyen (irsaliye_id, malzeme_tanim, mal_grubu, sap_kodu, otis_stok, otis_giris)
                VALUES (?, ?, '', ?, ?, ?)
            """, (k["irsaliye_id"], k["otis_malzeme_tanim"], k["otis_sap_kodu"] or "", k["otis_stok"], k["otis_giris"]))

        if _otis_sadece_kalem_mi(k):
            conn.execute("DELETE FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,))
            conn.execute("DELETE FROM fason_kalem WHERE id = ?", (kalem_id,))
            mesaj = "OTIS'ten açılan kalem silindi, kayıt bekleyenlere döndü"
        else:
            conn.execute("""
                UPDATE fason_kalem
                SET otis_malzeme_tanim = NULL, otis_sap_kodu = NULL, otis_stok = NULL, otis_giris = NULL,
                    otis_stok_onceki = NULL, otis_stok_son_degisim_tarihi = NULL,
                    otis_eslesme_tipi = NULL, otis_eslesme_tarihi = NULL, otis_eslesen_kullanici = NULL
                WHERE id = ?
            """, (kalem_id,))
            mesaj = "Eşleşme kaldırıldı, OTIS kaydı bekleyenlere döndü"
        conn.commit()
        log_kaydet("OTIS Eşleşme Kaldır", f"{k['otis_malzeme_tanim']} ↛ {k['malzeme_tanim']}", kalem_id, k["irsaliye_no"])
        return jsonify({"durum": "ok", "mesaj": mesaj})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/tum-kalemler", methods=["GET"])
def api_fason_tum_kalemler():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    ara = request.args.get("ara", "").strip()
    cikis_irs_ara = request.args.get("cikis_irs", "").strip()

    sorgu = """
        SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.stok_miktari, k.giris_miktari,
               k.otis_stok, k.otis_giris, k.fark_sebebi, k.durum, k.cikis_irsaliye_no,
               NULLIF(k.belge_tarihi, '') AS giris_tarihi,
               i.id AS irsaliye_id, i.irsaliye_no, f.ad AS firma_ad
        FROM fason_kalem k
        JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        LEFT JOIN fason_firma f ON f.id = i.firma_id
        WHERE 1=1
    """
    params = []
    if ara:
        sorgu += " AND k.malzeme_tanim LIKE ?"
        params.append(f"%{ara}%")
    if cikis_irs_ara:
        sorgu += " AND k.cikis_irsaliye_no LIKE ?"
        params.append(f"%{cikis_irs_ara}%")
    sorgu += " ORDER BY k.malzeme_tanim, i.irsaliye_no"

    conn = get_db()
    try:
        rows = conn.execute(sorgu, params).fetchall()
        stoklar = _sap_stoklari(conn, [r["sap_kodu"] for r in rows]) if rows else {}
        sonuc = []
        for r in rows:
            d = dict(r)
            d["stok_miktari"], d["sap_cikis"] = _sap_stok_degeri(r, stoklar)
            sonuc.append(d)
        return jsonify(sonuc)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()

@fason_bp.route("/api/fason/genel-kalem-temizle", methods=["POST"])
def api_fason_genel_kalem_temizle():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("rol") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Sadece admin"}), 403

    conn = get_db()
    try:
        # Yanında başka gerçek kalemi olan, boş "Genel Kalem" satırlarını sil
        silinecekler = conn.execute("""
            SELECT gk.id FROM fason_kalem gk
            WHERE gk.malzeme_tanim = 'Genel Kalem'
              AND (gk.sap_kodu IS NULL OR gk.sap_kodu = '')
              AND (
                SELECT COUNT(*) FROM fason_kalem k2
                WHERE k2.irsaliye_id = gk.irsaliye_id AND k2.id != gk.id
              ) > 0
        """).fetchall()
        silinen_idler = [r["id"] for r in silinecekler]
        if silinen_idler:
            ph = ",".join("?" * len(silinen_idler))
            conn.execute(f"DELETE FROM fason_kalem WHERE id IN ({ph})", silinen_idler)
        conn.commit()
        log_kaydet("Genel Kalem Temizliği", f"{len(silinen_idler)} çift kalem temizlendi", None, "")
        return jsonify({"durum": "ok", "silinen": len(silinen_idler), "mesaj": f"{len(silinen_idler)} çift 'Genel Kalem' temizlendi"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()

@fason_bp.route("/api/fason/irsaliye/<int:irs_id>/stok-export", methods=["GET"])
def api_fason_irsaliye_stok_export(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        conn = get_db()
        irs = conn.execute("SELECT irsaliye_no FROM fason_irsaliye WHERE id = ?", (irs_id,)).fetchone()
        if not irs:
            conn.close()
            return jsonify({"durum": "hata", "mesaj": "İrsaliye bulunamadı"}), 404
        kalemler = conn.execute(
            "SELECT id, malzeme_tanim, sap_kodu, stok_miktari, giris_miktari, durum, cikis_irsaliye_no, otis_giris, otis_stok FROM fason_kalem WHERE irsaliye_id = ? ORDER BY id",
            (irs_id,)
        ).fetchall()
        # Her kalemin çıkış/gönderim irsaliyeleri + miktarları + tarihleri (özet alanıyla aynı sıra; dönüşler hariç)
        hareket_miktarlari = {}
        hareket_tarihleri = {}
        for h in conn.execute("""
            SELECT c.kalem_id, c.irsaliye_no, c.miktar, c.tarih FROM fason_kalem_cikis c
            JOIN fason_kalem k ON k.id = c.kalem_id
            WHERE k.irsaliye_id = ? AND COALESCE(c.tip, 'cikis') <> 'tadilat_donus' AND c.irsaliye_no <> ''
            ORDER BY COALESCE(c.tarih, ''), c.id
        """, (irs_id,)).fetchall():
            liste = hareket_miktarlari.setdefault(h["kalem_id"], {})
            liste.setdefault(h["irsaliye_no"], h["miktar"])
            hareket_tarihleri.setdefault(h["kalem_id"], {}).setdefault(h["irsaliye_no"], h["tarih"])
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "Stok Kontrol"

        h_font = Font(bold=True, color="FFFFFF", size=11)
        h_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        kilit_fill = PatternFill("solid", fgColor="374151")
        edit_fill = PatternFill("solid", fgColor="065F46")
        border = Border(
            left=Side(style="thin", color="D1D5DB"), right=Side(style="thin", color="D1D5DB"),
            top=Side(style="thin", color="D1D5DB"), bottom=Side(style="thin", color="D1D5DB")
        )

        ws.merge_cells("A1:C1")
        ws["A1"] = f"İRSALİYE: {irs['irsaliye_no']} — SABİT (değiştirmeyin)"
        ws["A1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["A1"].fill = kilit_fill
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("D1:G1")
        ws["D1"] = "REFERANS (OTIS / SAP / Mevcut)"
        ws["D1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["D1"].fill = kilit_fill
        ws["D1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("H1:L1")
        ws["H1"] = "MB52'DEN KONTROL EDİP DOLDURUN"
        ws["H1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["H1"].fill = edit_fill
        ws["H1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22

        basliklar = [
            ("Kalem ID", "kilit"), ("Malzeme Tanım", "kilit"), ("SAP Kodu", "kilit"),
            ("OTIS Giriş", "kilit"), ("SAP Giriş", "kilit"), ("OTIS Stok", "kilit"), ("Mevcut Stok", "kilit"),
            ("Yeni Stok", "edit"), ("Çıkış İrsaliyesi", "edit"), ("Çıkış Miktarı", "edit"), ("Çıkış Tarihi", "edit"),
            ("Yeni Durum", "edit"),
        ]
        for col_idx, (baslik, tip) in enumerate(basliklar, start=1):
            cell = ws.cell(row=2, column=col_idx, value=baslik)
            cell.font = h_font
            cell.fill = kilit_fill if tip == "kilit" else edit_fill
            cell.alignment = h_align
            cell.border = border
        ws.row_dimensions[2].height = 28

        for i, w in enumerate([10, 45, 16, 12, 12, 12, 12, 12, 24, 18, 24, 14], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        thin_border = Border(
            left=Side(style="thin", color="E5E7EB"), right=Side(style="thin", color="E5E7EB"),
            top=Side(style="thin", color="E5E7EB"), bottom=Side(style="thin", color="E5E7EB")
        )
        for idx, k in enumerate(kalemler, start=3):
            values = [
                k["id"], k["malzeme_tanim"], k["sap_kodu"] or "",
                k["otis_giris"], k["giris_miktari"], k["otis_stok"], k["stok_miktari"],
                None, k["cikis_irsaliye_no"] or "", _cikis_miktar_hucresi(hareket_miktarlari.get(k["id"], {})),
                _cikis_tarih_hucresi(hareket_tarihleri.get(k["id"], {})),
                k["durum"] or "Stok",
            ]
            for c_idx, v in enumerate(values, start=1):
                cell = ws.cell(row=idx, column=c_idx, value=v)
                cell.border = thin_border
                if c_idx in (1, 4, 5, 6, 7, 8, 10):
                    cell.alignment = Alignment(horizontal="right")
                    if c_idx in (4, 5, 6, 7, 8, 10) and isinstance(v, (int, float)):
                        cell.number_format = "#,##0.00"
                elif c_idx == 11:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                    if isinstance(v, datetime):
                        cell.number_format = "DD.MM.YYYY"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
            if idx % 2 == 0:
                for c_idx in range(1, 13):
                    ws.cell(row=idx, column=c_idx).fill = PatternFill("solid", fgColor="F9FAFB")

        # Açıklama notu: birden fazla çıkış irsaliyesinde miktarlar ";" ile ayrılır
        ws.cell(row=1, column=14, value="Çıkış İrsaliyesi: birden fazlaysa virgülle (A, B). "
                "Çıkış Miktarı ve Çıkış Tarihi: aynı sırayla noktalı virgülle (116,46; 38,82 — 05.09.2026; 21.09.2026). "
                "Tek değer yazılırsa son irsaliyeye yazılır. Tarih boşsa bugünün tarihi alınır.").font = Font(italic=True, color="6B7280", size=9)

        durum_dv = DataValidation(type="list", formula1='"Stok,Çıkış,Çıkış (A63),Tadilat,Nakil,Aspro"', allow_blank=True)
        ws.add_data_validation(durum_dv)
        durum_dv.add(f"L3:L{2 + len(kalemler)}")

        ws.freeze_panes = "B3"

        klasor = os.path.join(_fason_export_klasor(), "exports", "fason_stok")
        os.makedirs(klasor, exist_ok=True)
        dosya_adi = f"stok_{irs['irsaliye_no']}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        yol = os.path.join(klasor, dosya_adi)
        wb.save(yol)
        try:
            os.startfile(klasor)
        except Exception:
            pass

        return jsonify({"durum": "ok", "kayit": len(kalemler), "dosya": dosya_adi, "mesaj": f"{len(kalemler)} kalem Excel'e aktarıldı"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/stok-import", methods=["POST"])
def api_fason_stok_import():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if "dosya" not in request.files:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    dosya = request.files["dosya"]
    if not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya adı boş"}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(dosya, data_only=True)
        ws = wb.active

        def _tr_norm(s):
            # Python'un .lower()'ı Türkçe "İ" harfini "i" + görünmez nokta karakterine
            # çevirdiği için (i̇), bu görünmez karakteri temizleyip normal karşılaştırma yapıyoruz
            return (str(s) if s is not None else "").strip().lower().replace("\u0307", "")

        h_row = None
        for row_idx in range(1, 5):
            row = [_tr_norm(c.value) for c in ws[row_idx]]
            if "kalem id" in row:
                h_row = row_idx
                break
        if h_row is None:
            return jsonify({"durum": "hata", "mesaj": "Başlık satırında 'Kalem ID' bulunamadı"}), 400

        basliklar = [_tr_norm(c.value) for c in ws[h_row]]
        col_id = basliklar.index("kalem id") + 1 if "kalem id" in basliklar else -1
        col_stok = basliklar.index("yeni stok") + 1 if "yeni stok" in basliklar else -1
        col_cikis_irs = basliklar.index("çıkış irsaliyesi") + 1 if "çıkış irsaliyesi" in basliklar else -1
        col_durum = basliklar.index("yeni durum") + 1 if "yeni durum" in basliklar else -1
        col_cikis_miktar = basliklar.index("çıkış miktarı") + 1 if "çıkış miktarı" in basliklar else -1
        col_cikis_tarih = basliklar.index("çıkış tarihi") + 1 if "çıkış tarihi" in basliklar else -1
        if col_id < 0 or col_stok < 0:
            return jsonify({"durum": "hata", "mesaj": "Kalem ID / Yeni Stok sütunları bulunamadı"}), 400

        def sayi(v):
            if v is None or v == "": return None
            try: return float(v)
            except Exception:
                try: return float(str(v).replace(".", "").replace(",", "."))
                except Exception: return None

        conn = get_db()
        guncellenen = 0
        kismi_cikis = 0
        miktar_guncellenen = 0
        tarih_guncellenen = 0
        tarih_hatalari = []
        degisiklikler = []
        try:
            for row_idx in range(h_row + 1, ws.max_row + 1):
                id_val = ws.cell(row=row_idx, column=col_id).value
                if id_val is None or id_val == "":
                    continue
                try:
                    kalem_id = int(id_val)
                except Exception:
                    continue
                yeni_stok = sayi(ws.cell(row=row_idx, column=col_stok).value)
                cikis_irs_deger = str(ws.cell(row=row_idx, column=col_cikis_irs).value or "").strip() if col_cikis_irs > 0 else ""
                durum_deger = str(ws.cell(row=row_idx, column=col_durum).value or "").strip() if col_durum > 0 else ""
                miktar_hucre = _miktar_listesi(ws.cell(row=row_idx, column=col_cikis_miktar).value) if col_cikis_miktar > 0 else []
                tarih_hucre = _tarih_listesi(ws.cell(row=row_idx, column=col_cikis_tarih).value) if col_cikis_tarih > 0 else []

                mevcut = conn.execute("SELECT id, malzeme_tanim, stok_miktari, durum, cikis_irsaliye_no FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
                if not mevcut:
                    continue

                # Çıkış irsaliyesi hücresi Excel'e özet olarak ("A, B") yazıldığı için aynen geri gelebilir —
                # sadece kalemde henüz OLMAYAN numaralar yeni çıkış olarak eklenir (eskiler silinmez/ezilmez).
                mevcut_nolar = {r[0] for r in conn.execute(
                    "SELECT irsaliye_no FROM fason_kalem_cikis WHERE kalem_id = ?", (kalem_id,)).fetchall()}
                hucre_nolar = _cikis_no_listesi(cikis_irs_deger)
                yeni_nolar = [n for n in hucre_nolar if n not in mevcut_nolar]
                stokta_kalan_var = yeni_stok is not None and yeni_stok > 0.005

                # "Çıkış Miktarı" hücresi: irsaliyelerle aynı sırada ';' ile ayrılmış miktarlar.
                # Tek miktar yazıldıysa hücredeki SON irsaliyeye ait sayılır.
                elle_miktar = {}
                if miktar_hucre and hucre_nolar:
                    if len(miktar_hucre) == len(hucre_nolar):
                        elle_miktar = {n: m for n, m in zip(hucre_nolar, miktar_hucre) if m is not None and m > 0}
                    elif len(miktar_hucre) == 1 and miktar_hucre[0] is not None and miktar_hucre[0] > 0:
                        elle_miktar = {hucre_nolar[-1]: miktar_hucre[0]}
                # "Çıkış Tarihi" hücresi: miktarla aynı kural (aynı sırayla ';', tek değer → son irsaliye)
                elle_tarih = {}
                if tarih_hucre and hucre_nolar:
                    eslesme = (list(zip(hucre_nolar, tarih_hucre)) if len(tarih_hucre) == len(hucre_nolar)
                               else [(hucre_nolar[-1], tarih_hucre[0])] if len(tarih_hucre) == 1 else [])
                    for n, t in eslesme:
                        if t and t.startswith("HATA:"):
                            tarih_hatalari.append(f"Satır {row_idx}: {t[5:]}")
                        elif t:
                            elle_tarih[n] = t
                # Var olan irsaliyelerin tarihi Excel'de değiştirildiyse güncelle
                tarih_degisen = []
                for no, t in elle_tarih.items():
                    if no in mevcut_nolar:
                        r = conn.execute("SELECT id, tarih FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?",
                                         (kalem_id, no)).fetchone()
                        if r and (r["tarih"] or "")[:10] != t:
                            tarih_degisen.append((r["id"], t))
                # Var olan irsaliyelerin miktarı Excel'de değiştirildiyse güncelle
                miktar_degisen = []
                for no, m in elle_miktar.items():
                    if no in mevcut_nolar:
                        r = conn.execute("SELECT id, miktar FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?",
                                         (kalem_id, no)).fetchone()
                        if r and (r["miktar"] is None or abs(r["miktar"] - m) > 0.0005):
                            miktar_degisen.append((r["id"], m))

                # Otomatik durum kuralı: Excel'de "Yeni Durum" elle girilmediyse,
                # yeni çıkış irsaliyesi girildiyse ya da stok 0'landıysa otomatik "Çıkış" say.
                # Çıkış irsaliyesi girilip stokta hâlâ malzeme kalıyorsa bu KISMİ çıkıştır → durum değişmez.
                yeni_durum = mevcut["durum"] or "Stok"
                if durum_deger in ("Stok", "Çıkış", "Çıkış (A63)", "Tadilat", "Nakil", "Aspro"):
                    yeni_durum = durum_deger
                elif mevcut["durum"] != "Çıkış (A63)" and yeni_nolar and not stokta_kalan_var:
                    yeni_durum = "Çıkış"
                elif mevcut["durum"] != "Çıkış (A63)" and yeni_stok is not None and abs(yeni_stok) < 0.005:
                    yeni_durum = "Çıkış"
                if yeni_nolar and stokta_kalan_var:
                    kismi_cikis += 1

                degisti = ((mevcut["stok_miktari"] != yeni_stok) or (mevcut["durum"] != yeni_durum)
                           or bool(yeni_nolar) or bool(miktar_degisen) or bool(tarih_degisen))
                if not degisti:
                    continue

                for cikis_id, m in miktar_degisen:
                    conn.execute("UPDATE fason_kalem_cikis SET miktar = ?, kaynak = 'excel' WHERE id = ?", (m, cikis_id))
                    miktar_guncellenen += 1
                for cikis_id, t in tarih_degisen:
                    conn.execute("UPDATE fason_kalem_cikis SET tarih = ? WHERE id = ?", (t, cikis_id))
                    tarih_guncellenen += 1
                if tarih_degisen:
                    _cikis_ozet_guncelle(conn, kalem_id)   # özet tarih sırasıyla yazılıyor

                if yeni_nolar:
                    # Hareket tipi: Excel'de "Yeni Durum" Tadilat/Nakil/A63 yazıldıysa ona göre, yoksa kesin çıkış
                    hareket_tipi = DURUM_HAREKET_TIPI.get(durum_deger, "cikis")
                    # Tek yeni irsaliye geldiyse çıkan miktar = stoktaki düşüş (yoksa, stok sıfırlandıysa kalanın tamamı)
                    cikis_miktar = None
                    if len(yeni_nolar) == 1:
                        if mevcut["stok_miktari"] is not None and yeni_stok is not None and mevcut["stok_miktari"] - yeni_stok > 0.005:
                            cikis_miktar = round(mevcut["stok_miktari"] - yeni_stok, 3)
                        elif yeni_stok is not None and abs(yeni_stok) < 0.005:
                            oz = _cikis_ozet_hesapla(conn, kalem_id)
                            aday = oz["kalan"] if hareket_tipi in KESIN_CIKIS_TIPLERI else oz["depoda"]
                            if aday is not None and aday > 0.005:
                                cikis_miktar = aday
                    for no in yeni_nolar:
                        # Excel'de miktar yazıldıysa o, yoksa (tek yeni irsaliyede) stok düşüşünden tahmin
                        m = elle_miktar.get(no, cikis_miktar if len(yeni_nolar) == 1 else None)
                        _cikis_ekle(conn, kalem_id, no, m, tarih=elle_tarih.get(no), kaynak="mb52",
                                    kullanici=session.get("kullanici", ""), tip=hareket_tipi)
                    _cikis_ozet_guncelle(conn, kalem_id)

                conn.execute(
                    "UPDATE fason_kalem SET stok_miktari = ?, durum = ? WHERE id = ?",
                    (yeni_stok, yeni_durum, kalem_id)
                )
                guncellenen += 1
                degisiklikler.append({
                    "malzeme_tanim": mevcut["malzeme_tanim"],
                    "eski_stok": mevcut["stok_miktari"],
                    "yeni_stok": yeni_stok,
                    "yeni_durum": yeni_durum
                })
            conn.commit()
        finally:
            conn.close()

        log_kaydet("Toplu Stok Import (MB52)", f"{dosya.filename}: {guncellenen} kalem güncellendi", None, dosya.filename)

        return jsonify({
            "durum": "ok", "guncellenen": guncellenen, "degisiklikler": degisiklikler[:100],
            "kismi_cikis": kismi_cikis, "miktar_guncellenen": miktar_guncellenen,
            "tarih_guncellenen": tarih_guncellenen, "tarih_hatalari": tarih_hatalari[:50],
            "mesaj": f"{guncellenen} kalem güncellendi"
                     + (f" · {miktar_guncellenen} çıkış miktarı güncellendi" if miktar_guncellenen else "")
                     + (f" · {tarih_guncellenen} çıkış tarihi güncellendi" if tarih_guncellenen else "")
                     + (f" · {len(tarih_hatalari)} tarih anlaşılamadı (o irsaliyelerde bugünün tarihi alındı ya da eski tarih korundu)" if tarih_hatalari else "")
                     + (f" · {kismi_cikis} kalemde kısmi çıkış (stokta malzeme kaldı, durum değiştirilmedi)" if kismi_cikis else "")
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

# ═════════════════════════════════════════════════
# ÇIKIŞ MİKTARLARI — tüm hareketleri tek Excel'de toplu doldur (eski kayıtlar için)
# ═════════════════════════════════════════════════

_HAREKET_KAPSAM_SQL = {
    "eksik": "c.miktar IS NULL",
    "tahmini": "(c.miktar IS NULL OR c.kaynak = 'aktarim')",
    "tumu": "1=1",
}


@fason_bp.route("/api/fason/hareket-sayilari", methods=["GET"])
def api_fason_hareket_sayilari():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        temel = "SELECT COUNT(*) FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id WHERE "
        return jsonify({ad: conn.execute(temel + kosul).fetchone()[0] for ad, kosul in _HAREKET_KAPSAM_SQL.items()})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/hareket-export", methods=["GET"])
def api_fason_hareket_export():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    kapsam = request.args.get("kapsam", "tahmini")
    if kapsam not in _HAREKET_KAPSAM_SQL:
        kapsam = "tahmini"
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        conn = get_db()
        try:
            rows = conn.execute(f"""
                SELECT c.id, i.irsaliye_no, COALESCE(f.ad, '') AS firma, k.malzeme_tanim, k.sap_kodu,
                       k.giris_miktari, k.stok_miktari, k.durum, COALESCE(c.tip, 'cikis') AS tip,
                       c.irsaliye_no AS cikis_no, c.miktar, c.kaynak, c.tarih
                FROM fason_kalem_cikis c
                JOIN fason_kalem k ON k.id = c.kalem_id
                JOIN fason_irsaliye i ON i.id = k.irsaliye_id
                LEFT JOIN fason_firma f ON f.id = i.firma_id
                WHERE {_HAREKET_KAPSAM_SQL[kapsam]}
                ORDER BY i.irsaliye_no, k.id, COALESCE(c.tarih, ''), c.id
            """).fetchall()
        finally:
            conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = "Çıkış Miktarları"
        kilit_fill = PatternFill("solid", fgColor="374151")
        edit_fill = PatternFill("solid", fgColor="065F46")
        h_font = Font(bold=True, color="FFFFFF", size=11)
        h_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ince = Border(*(Side(style="thin", color="E5E7EB"),) * 4)

        ws.merge_cells("A1:J1")
        ws["A1"] = "BİLGİ — değiştirmeyin"
        ws.merge_cells("K1:M1")
        ws["K1"] = "DOLDURUN / DÜZELTİN"
        for hucre, fill in (("A1", kilit_fill), ("K1", edit_fill)):
            ws[hucre].font = Font(bold=True, color="FFFFFF", size=10)
            ws[hucre].fill = fill
            ws[hucre].alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row=1, column=15, value="Yeni Miktar boş bırakılırsa o satır değişmez. Hareket Tipi listeden seçilebilir.").font = Font(italic=True, color="6B7280", size=9)

        basliklar = [("Hareket ID", "k"), ("Fason İrsaliye", "k"), ("Firma", "k"), ("Malzeme Tanım", "k"), ("SAP Kodu", "k"),
                     ("SAP Giriş", "k"), ("Durum", "k"), ("Çıkış İrsaliye No", "k"), ("Mevcut Miktar", "k"), ("Kaynak", "k"),
                     ("Hareket Tipi", "e"), ("Yeni Miktar", "e"), ("Tarih", "e")]
        for i, (b, t) in enumerate(basliklar, start=1):
            c = ws.cell(row=2, column=i, value=b)
            c.font, c.alignment, c.border = h_font, h_align, ince
            c.fill = kilit_fill if t == "k" else edit_fill
        for i, w in enumerate([11, 20, 16, 42, 14, 11, 12, 20, 13, 20, 24, 13, 12], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        kaynak_etiket = {"aktarim": "Eski kayıttan (tahmini)", "elle": "Elle", "toplu": "Toplu işlem",
                         "mb52": "MB52 stok", "excel": "Excel"}
        tarih_fmt = "DD.MM.YYYY"
        for r_idx, r in enumerate(rows, start=3):
            tarih = None
            if r["tarih"]:
                try:
                    tarih = datetime.strptime(r["tarih"][:10], "%Y-%m-%d")
                except ValueError:
                    tarih = r["tarih"]
            degerler = [r["id"], r["irsaliye_no"], r["firma"], r["malzeme_tanim"], r["sap_kodu"] or "",
                        r["giris_miktari"], r["durum"] or "", r["cikis_no"], r["miktar"],
                        kaynak_etiket.get(r["kaynak"], r["kaynak"] or ""),
                        HAREKET_TIPLERI.get(r["tip"], r["tip"]), None, tarih]
            for c_idx, v in enumerate(degerler, start=1):
                c = ws.cell(row=r_idx, column=c_idx, value=v)
                c.border = ince
                if c_idx in (6, 9, 12):
                    c.number_format = "#,##0.00"
                if c_idx == 13 and isinstance(v, datetime):
                    c.number_format = tarih_fmt
            if r["miktar"] is None:
                ws.cell(row=r_idx, column=12).fill = PatternFill("solid", fgColor="FEF3C7")   # boş → sarı vurgula

        from openpyxl.worksheet.datavalidation import DataValidation as _DV
        tip_dv = _DV(type="list", formula1='"' + ",".join(HAREKET_TIPLERI.values()) + '"', allow_blank=False)
        ws.add_data_validation(tip_dv)
        if rows:
            tip_dv.add(f"K3:K{2 + len(rows)}")
        ws.freeze_panes = "E3"
        ws.auto_filter.ref = f"A2:M{2 + max(len(rows), 1)}"

        klasor = os.path.join(_fason_export_klasor(), "exports", "fason_hareket")
        os.makedirs(klasor, exist_ok=True)
        dosya_adi = f"cikis_miktarlari_{kapsam}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        wb.save(os.path.join(klasor, dosya_adi))
        try:
            os.startfile(klasor)
        except Exception:
            pass
        return jsonify({"durum": "ok", "kayit": len(rows), "dosya": dosya_adi,
                        "mesaj": f"{len(rows)} hareket Excel'e aktarıldı ({dosya_adi})"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/hareket-import", methods=["POST"])
def api_fason_hareket_import():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    dosya = request.files.get("dosya")
    if not dosya or not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya yok"}), 400
    try:
        from openpyxl import load_workbook
        ws = load_workbook(dosya, data_only=True).active

        norm = lambda v: (str(v) if v is not None else "").strip().lower().replace("̇", "")
        h_row = next((r for r in range(1, 6) if "hareket id" in [norm(c.value) for c in ws[r]]), None)
        if h_row is None:
            return jsonify({"durum": "hata", "mesaj": "Başlıkta 'Hareket ID' bulunamadı — 'Çıkış Miktarları' Excel'ini kullan"}), 400
        bas = [norm(c.value) for c in ws[h_row]]
        kol = lambda ad: bas.index(ad) + 1 if ad in bas else -1
        c_id, c_miktar, c_tip, c_tarih = kol("hareket id"), kol("yeni miktar"), kol("hareket tipi"), kol("tarih")
        if c_miktar < 0:
            return jsonify({"durum": "hata", "mesaj": "'Yeni Miktar' sütunu bulunamadı"}), 400
        etiket_tip = {norm(v): k for k, v in HAREKET_TIPLERI.items()}

        conn = get_db()
        miktar_say = tip_say = tarih_say = 0
        hatalar = []
        etkilenen_kalemler = set()
        try:
            for r_idx in range(h_row + 1, ws.max_row + 1):
                hid = ws.cell(row=r_idx, column=c_id).value
                if hid in (None, ""):
                    continue
                try:
                    hid = int(hid)
                except (TypeError, ValueError):
                    continue
                mevcut = conn.execute("SELECT id, kalem_id, miktar, COALESCE(tip, 'cikis') AS tip, tarih FROM fason_kalem_cikis WHERE id = ?",
                                      (hid,)).fetchone()
                if not mevcut:
                    hatalar.append(f"Satır {r_idx}: hareket {hid} bulunamadı (silinmiş olabilir)")
                    continue
                set_parts, vals = [], []

                ham = ws.cell(row=r_idx, column=c_miktar).value
                if ham not in (None, ""):
                    try:
                        m = _tr_sayi(ham)
                    except ValueError:
                        hatalar.append(f"Satır {r_idx}: geçersiz miktar '{ham}'")
                        m = None
                    if m is not None:
                        if m <= 0:
                            hatalar.append(f"Satır {r_idx}: miktar 0'dan büyük olmalı")
                        elif mevcut["miktar"] is None or abs(mevcut["miktar"] - m) > 0.0005:
                            set_parts += ["miktar = ?", "kaynak = 'excel'"]; vals.append(m); miktar_say += 1

                if c_tip > 0:
                    t_ham = ws.cell(row=r_idx, column=c_tip).value
                    if t_ham not in (None, ""):
                        tip = etiket_tip.get(norm(t_ham)) or (norm(t_ham) if norm(t_ham) in HAREKET_TIPLERI else None)
                        if not tip:
                            hatalar.append(f"Satır {r_idx}: tanınmayan hareket tipi '{t_ham}'")
                        elif tip != mevcut["tip"]:
                            set_parts.append("tip = ?"); vals.append(tip); tip_say += 1

                if c_tarih > 0:
                    t = ws.cell(row=r_idx, column=c_tarih).value
                    t_str = None
                    if hasattr(t, "strftime"):
                        t_str = t.strftime("%Y-%m-%d")
                    elif t not in (None, ""):
                        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
                            try:
                                t_str = datetime.strptime(str(t).strip(), fmt).strftime("%Y-%m-%d"); break
                            except ValueError:
                                pass
                        if t_str is None:
                            hatalar.append(f"Satır {r_idx}: tarih anlaşılamadı '{t}'")
                    if t_str and t_str != (mevcut["tarih"] or "")[:10]:
                        set_parts.append("tarih = ?"); vals.append(t_str); tarih_say += 1

                if set_parts:
                    vals.append(hid)
                    conn.execute(f"UPDATE fason_kalem_cikis SET {', '.join(set_parts)} WHERE id = ?", vals)
                    etkilenen_kalemler.add(mevcut["kalem_id"])
            for kid in etkilenen_kalemler:
                _cikis_ozet_guncelle(conn, kid)
            conn.commit()
        finally:
            conn.close()

        log_kaydet("Çıkış Miktarları Import",
                   f"{dosya.filename}: {miktar_say} miktar, {tip_say} tip, {tarih_say} tarih güncellendi ({len(etkilenen_kalemler)} kalem)",
                   None, dosya.filename)
        parcalar = [f"{miktar_say} miktar"] + ([f"{tip_say} hareket tipi"] if tip_say else []) + ([f"{tarih_say} tarih"] if tarih_say else [])
        return jsonify({"durum": "ok", "miktar": miktar_say, "tip": tip_say, "tarih": tarih_say,
                        "kalem": len(etkilenen_kalemler), "hatalar": hatalar[:50],
                        "mesaj": ", ".join(parcalar) + f" güncellendi ({len(etkilenen_kalemler)} kalem)"
                                 + (f" · {len(hatalar)} satırda sorun var" if hatalar else "")})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ═════════════════════════════════════════════════
# ÇIKIŞ GİR (toplu hareket) + EKSİK HAREKETLER iş listesi
# ═════════════════════════════════════════════════

_OTIS_TOL_KG = 0.5      # OTIS'te çıkan ile kayıtlı hareket arasındaki farkta tolerans (kg)
_OTIS_TOL_ORAN = 0.01   # ... ya da OTIS girişinin %1'i (hangisi büyükse)


def _kalem_hareket_ozetleri(conn, where_sql="1=1", params=(), limit=None, order_sql="i.irsaliye_no, k.id"):
    """Kalem + irsaliye + firma + hareket toplamları + konum özeti (kalan/depoda/tadilatta/nakilde) + OTIS çıkanı."""
    sql = f"""
        SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.durum, k.otis_giris, k.otis_stok,
               NULLIF(k.belge_tarihi, '') AS giris_tarihi,
               i.id AS irsaliye_id, i.irsaliye_no, i.firma_id, COALESCE(f.ad, 'Firma Yok') AS firma,
               COALESCE(h.hareket_sayisi, 0) AS hareket_sayisi, h.kesin, h.gidis, h.donus, h.nakil,
               COALESCE(h.miktari_eksik, 0) AS miktari_eksik
        FROM fason_kalem k
        JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        LEFT JOIN fason_firma f ON f.id = i.firma_id
        LEFT JOIN ({_HAREKET_TOPLAM_SQL}) h ON h.kalem_id = k.id
        WHERE {where_sql}
        ORDER BY {order_sql}
    """ + (f" LIMIT {int(limit)}" if limit else "")
    sonuc = []
    for r in conn.execute(sql, list(params)).fetchall():
        oz = _hareket_hesapla(r["giris_miktari"], r["kesin"] or 0, r["gidis"] or 0, r["donus"] or 0, r["nakil"] or 0,
                              bool(r["miktari_eksik"]))
        if not r["hareket_sayisi"] and r["giris_miktari"] is not None:
            oz.update(kalan=r["giris_miktari"], depoda=r["giris_miktari"], tadilatta=0.0, nakilde=0.0)
        kayitli_cikan = (r["kesin"] or 0) + (r["nakil"] or 0) + (r["gidis"] or 0) - (r["donus"] or 0)
        otis_cikan = (r["otis_giris"] - r["otis_stok"]) if (r["otis_giris"] is not None and r["otis_stok"] is not None) else None
        # OTIS kantar ağırlığıyla, SAP/hareketler SAP miktarıyla tutulur (kantar farkı olabilir) — karşılaştırma için
        # OTIS'te çıkanı SAP ölçeğine çevir: OTIS çıkan × SAP giriş / OTIS giriş (yani OTIS'te çıkan ORAN)
        otis_cikan_sap = otis_cikan
        if otis_cikan is not None and r["otis_giris"] and r["giris_miktari"]:
            otis_cikan_sap = otis_cikan * r["giris_miktari"] / r["otis_giris"]
        sonuc.append({
            "id": r["id"], "malzeme_tanim": r["malzeme_tanim"], "sap_kodu": r["sap_kodu"] or "",
            "giris": r["giris_miktari"], "durum": r["durum"] or "", "giris_tarihi": r["giris_tarihi"],
            "irsaliye_id": r["irsaliye_id"], "irsaliye_no": r["irsaliye_no"], "firma_id": r["firma_id"], "firma": r["firma"],
            "hareket_sayisi": r["hareket_sayisi"], "miktari_eksik": bool(r["miktari_eksik"]),
            "kalan": oz["kalan"], "depoda": oz["depoda"], "tadilatta": oz["tadilatta"], "nakilde": oz["nakilde"],
            "otis_giris": r["otis_giris"], "otis_stok": r["otis_stok"],
            "otis_cikan": round(otis_cikan, 3) if otis_cikan is not None else None,
            "otis_cikan_sap": round(otis_cikan_sap, 3) if otis_cikan_sap is not None else None,
            "kayitli_cikan": round(kayitli_cikan, 3),
        })
    return sonuc


def _onerilen_miktar(k, tip):
    """Hareket tipine göre önerilen miktar: kesin çıkışta kalan, gönderimde depodaki, dönüşte tadilattaki."""
    v = k["kalan"] if tip in KESIN_CIKIS_TIPLERI else (k["tadilatta"] if tip == "tadilat_donus" else k["depoda"])
    return round(v, 3) if v is not None and v > 0.005 else None


@fason_bp.route("/api/fason/hareket/kalem-ara", methods=["GET"])
def api_fason_hareket_kalem_ara():
    """Çıkış Gir ekranı için kalem arama (SAP kodu / malzeme tanımı / fason irsaliye no)."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    q = (request.args.get("q") or "").strip()
    tip = (request.args.get("tip") or "cikis").strip()
    if len(q) < 2:
        return jsonify({"kalemler": []})
    conn = get_db()
    try:
        like = f"%{q}%"
        # Parametre sırası: WHERE'deki 3 LIKE, ardından ORDER BY'daki tam eşleşme (SAP kodu birebir olan en üstte)
        kalemler = _kalem_hareket_ozetleri(
            conn, "(k.sap_kodu LIKE ? OR k.malzeme_tanim LIKE ? OR i.irsaliye_no LIKE ?)", (like, like, like, q),
            limit=40, order_sql="CASE WHEN k.sap_kodu = ? THEN 0 ELSE 1 END, i.irsaliye_no, k.id")
        for k in kalemler:
            k["onerilen"] = _onerilen_miktar(k, tip)
        return jsonify({"kalemler": kalemler})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/hareket/kalemler", methods=["POST"])
def api_fason_hareket_kalemler():
    """Verilen kalem id'lerinin güncel özetleri (iş listesinden Çıkış Gir'e aktarırken)."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    d = request.get_json() or {}
    tip = (d.get("tip") or "cikis").strip()
    try:
        idler = [int(x) for x in (d.get("idler") or [])][:2000]
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "Geçersiz kalem listesi"}), 400
    if not idler:
        return jsonify({"kalemler": []})
    conn = get_db()
    try:
        sonuc = []
        for i in range(0, len(idler), 900):
            parca = idler[i:i + 900]
            sonuc += _kalem_hareket_ozetleri(conn, f"k.id IN ({','.join('?' * len(parca))})", parca)
        sira = {kid: n for n, kid in enumerate(idler)}
        sonuc.sort(key=lambda k: sira.get(k["id"], 0))
        for k in sonuc:
            k["onerilen"] = _onerilen_miktar(k, tip)
        return jsonify({"kalemler": sonuc})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/hareket/eslestir", methods=["POST"])
def api_fason_hareket_eslestir():
    """Excel'den yapıştırılan 'SAP kodu (veya malzeme tanımı) [TAB] miktar' satırlarını kalemlerle eşleştirir.
    Aynı SAP koduna birden fazla kalem düşerse: önce kalanı olanlar; hâlâ birden fazlaysa seçim istenir."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    d = request.get_json() or {}
    tip = (d.get("tip") or "cikis").strip()
    metin = str(d.get("metin") or "")
    satirlar = []
    for ham in metin.splitlines():
        if not ham.strip():
            continue
        parca = [p.strip() for p in (ham.split("\t") if "\t" in ham else ham.split(";"))]
        anahtar = parca[0]
        miktar = None
        if len(parca) > 1 and parca[1]:
            try:
                miktar = _tr_sayi(parca[1])
            except ValueError:
                miktar = None
        satirlar.append((ham.strip(), anahtar, miktar))
    if not satirlar:
        return jsonify({"satirlar": []})
    conn = get_db()
    try:
        sonuc = []
        for ham, anahtar, miktar in satirlar[:1000]:
            adaylar = _kalem_hareket_ozetleri(conn, "(k.sap_kodu = ? OR k.malzeme_tanim = ?)", (anahtar, anahtar))
            for a in adaylar:
                a["onerilen"] = _onerilen_miktar(a, tip)
            uygun = [a for a in adaylar if a["onerilen"]] or adaylar
            if not adaylar:
                sonuc.append({"satir": ham, "anahtar": anahtar, "miktar": miktar, "durum": "bulunamadi", "adaylar": []})
            elif len(uygun) == 1:
                sonuc.append({"satir": ham, "anahtar": anahtar, "miktar": miktar, "durum": "tek", "kalem": uygun[0]})
            else:
                sonuc.append({"satir": ham, "anahtar": anahtar, "miktar": miktar, "durum": "secim", "adaylar": uygun[:20]})
        return jsonify({"satirlar": sonuc})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/hareket/irsaliye", methods=["GET"])
def api_fason_hareket_irsaliye():
    """Bu çıkış irsaliye no'su ile daha önce girilmiş hareketler (mükerrer giriş uyarısı için)."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    no = (request.args.get("no") or "").strip()
    if not no:
        return jsonify({"hareketler": []})
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT c.id, c.kalem_id, c.miktar, c.tarih, COALESCE(c.tip, 'cikis') AS tip,
                   k.malzeme_tanim, k.sap_kodu, i.irsaliye_no AS fason_irsaliye
            FROM fason_kalem_cikis c
            JOIN fason_kalem k ON k.id = c.kalem_id JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE c.irsaliye_no = ? ORDER BY c.id
        """, (no,)).fetchall()
        return jsonify({"hareketler": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/hareket/toplu", methods=["POST"])
def api_fason_hareket_toplu():
    """Tek çıkış irsaliyesi (no + tarih + tip) altında birden fazla kalemi tek seferde kaydeder.
    Kalemde bu irsaliye zaten varsa yeni kayıt açılmaz; miktar/tarih/tip güncellenir."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    d = request.get_json() or {}
    tip = (d.get("tip") or "").strip()
    if tip not in HAREKET_TIPLERI:
        return jsonify({"durum": "hata", "mesaj": "Hareket tipi seç"}), 400
    no = (d.get("irsaliye_no") or "").strip()
    if not no and tip != "tadilat_donus":
        return jsonify({"durum": "hata", "mesaj": "İrsaliye no zorunlu"}), 400
    if len(no) > 50:
        return jsonify({"durum": "hata", "mesaj": "İrsaliye no çok uzun"}), 400
    try:
        tarih = _tarih_iso(d.get("tarih"))
    except ValueError:
        return jsonify({"durum": "hata", "mesaj": "Tarih anlaşılamadı"}), 400
    if not tarih:
        return jsonify({"durum": "hata", "mesaj": "Tarih zorunlu"}), 400

    satirlar = d.get("satirlar") or []
    if not satirlar:
        return jsonify({"durum": "hata", "mesaj": "Kalem eklenmedi"}), 400
    temiz = []
    for s in satirlar:
        try:
            kid = int(s.get("kalem_id"))
            m = _tr_sayi(s.get("miktar"))
        except (TypeError, ValueError):
            return jsonify({"durum": "hata", "mesaj": "Geçersiz kalem/miktar"}), 400
        if m is None or m <= 0:
            return jsonify({"durum": "hata", "mesaj": "Her kalem için 0'dan büyük miktar gir"}), 400
        temiz.append((kid, m))
    if len({k for k, _ in temiz}) != len(temiz):
        return jsonify({"durum": "hata", "mesaj": "Aynı kalem listede iki kez var"}), 400

    kullanici = session.get("kullanici", "")
    conn = get_db()
    try:
        eklenen = guncellenen = 0
        durum_degisen = []
        for kid, m in temiz:
            k = conn.execute("SELECT id, malzeme_tanim FROM fason_kalem WHERE id = ?", (kid,)).fetchone()
            if not k:
                conn.rollback()
                return jsonify({"durum": "hata", "mesaj": f"Kalem bulunamadı (id {kid})"}), 404
            var = conn.execute("SELECT id FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ? AND ? <> ''",
                               (kid, no, no)).fetchone()
            if var:
                conn.execute("UPDATE fason_kalem_cikis SET miktar = ?, tarih = ?, tip = ? WHERE id = ?", (m, tarih, tip, var["id"]))
                guncellenen += 1
            else:
                conn.execute("""
                    INSERT INTO fason_kalem_cikis (kalem_id, irsaliye_no, miktar, tarih, kaynak, ekleyen, tip)
                    VALUES (?, ?, ?, ?, 'toplu', ?, ?)
                """, (kid, no, m, tarih, kullanici, tip))
                eklenen += 1
            _cikis_ozet_guncelle(conn, kid)
            yeni = _hareket_sonrasi_durum(conn, kid, tip)
            if yeni:
                durum_degisen.append({"kalem_id": kid, "malzeme_tanim": k["malzeme_tanim"], "durum": yeni})
        conn.commit()
        log_kaydet("Toplu Hareket Girişi",
                   f"{HAREKET_TIPLERI[tip]} {no} ({tarih}): {eklenen} eklendi, {guncellenen} güncellendi", None, no)
        mesaj = f"{HAREKET_TIPLERI[tip]} {no}: {eklenen} kalem eklendi" + (f", {guncellenen} güncellendi" if guncellenen else "")
        if durum_degisen:
            mesaj += f" · {len(durum_degisen)} kalemin durumu güncellendi"
        return jsonify({"durum": "ok", "eklenen": eklenen, "guncellenen": guncellenen,
                        "durum_degisen": durum_degisen, "mesaj": mesaj})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


_EKSIK_LISTELER = ("otis", "irsaliyesiz", "kalanli", "miktarsiz", "tarihsiz")


@fason_bp.route("/api/fason/eksik-hareketler", methods=["GET"])
def api_fason_eksik_hareketler():
    """Geriye dönük girilmesi gereken hareketlerin iş listesi.
      otis        : OTIS'te çıkmış (OTIS giriş − OTIS stok) ama sistemde kayıtlı hareket bunu karşılamıyor
      irsaliyesiz : durumu Çıkış / A63 ama hiç hareketi yok
      kalanli     : durumu Çıkış / A63 ama kesin çıkan girişten az
      miktarsiz   : miktarı girilmemiş hareketler (hareket satırı bazında)
      tarihsiz    : tarihi girilmemiş hareketler (hareket satırı bazında)"""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    liste = request.args.get("liste", "otis")
    if liste not in _EKSIK_LISTELER:
        liste = "otis"
    firma = (request.args.get("firma") or "").strip()
    ara = (request.args.get("ara") or "").strip()
    try:
        limit = max(1, min(3000, int(request.args.get("limit", 500))))
    except ValueError:
        limit = 500

    kosul, params = ["1=1"], []
    if firma == "__yok__":
        kosul.append("i.firma_id IS NULL")
    elif firma:
        try:
            params.append(int(firma)); kosul.append("i.firma_id = ?")
        except ValueError:
            pass
    if ara:
        kosul.append("(i.irsaliye_no LIKE ? OR k.malzeme_tanim LIKE ? OR k.sap_kodu LIKE ?)")
        params += [f"%{ara}%"] * 3
    where = " AND ".join(kosul)

    conn = get_db()
    try:
        kalemler = _kalem_hareket_ozetleri(conn, where, params)
        kesin_durum = ("Çıkış", "Çıkış (A63)")

        def otis_eksik(k):
            # Durumu elle "Stok" verilmiş kalem: malzemenin depoda olduğu teyit edilmiş — eksik hareket sayılmaz
            if k["otis_cikan"] is None or k["durum"] == "Stok":
                return None
            fark = k["otis_cikan_sap"] - k["kayitli_cikan"]          # kantar farkından arındırılmış
            tol = max(_OTIS_TOL_KG, _OTIS_TOL_ORAN * (k["giris"] or k["otis_giris"] or 0))
            return round(fark, 3) if fark > tol else None

        gruplar = {
            "otis": [], "irsaliyesiz": [], "kalanli": [],
        }
        for k in kalemler:
            f = otis_eksik(k)
            if f is not None:
                gruplar["otis"].append(dict(k, eksik=f))
            if k["durum"] in kesin_durum and not k["hareket_sayisi"]:
                gruplar["irsaliyesiz"].append(dict(k, eksik=k["giris"]))
            elif (k["durum"] in kesin_durum and k["hareket_sayisi"] and not k["miktari_eksik"]
                  and k["kalan"] is not None and k["kalan"] > 0.005):
                gruplar["kalanli"].append(dict(k, eksik=round(k["kalan"], 3)))

        hareket_sql = f"""
            SELECT c.id AS hareket_id, c.kalem_id AS id, c.irsaliye_no AS cikis_irsaliye, c.miktar, c.tarih,
                   COALESCE(c.tip, 'cikis') AS tip, c.kaynak,
                   k.malzeme_tanim, k.sap_kodu, k.giris_miktari AS giris, k.durum, NULLIF(k.belge_tarihi, '') AS giris_tarihi,
                   i.id AS irsaliye_id, i.irsaliye_no, COALESCE(f.ad, 'Firma Yok') AS firma
            FROM fason_kalem_cikis c
            JOIN fason_kalem k ON k.id = c.kalem_id
            JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            LEFT JOIN fason_firma f ON f.id = i.firma_id
            WHERE {where} AND {{kosul}}
            ORDER BY i.irsaliye_no, k.id, c.id
        """
        say = lambda kos: conn.execute(f"SELECT COUNT(*) FROM ({hareket_sql.format(kosul=kos)})", params).fetchone()[0]
        sayilar = {ad: len(v) for ad, v in gruplar.items()}
        sayilar["miktarsiz"] = say("c.miktar IS NULL")
        sayilar["tarihsiz"] = say("COALESCE(c.tarih, '') = ''")
        kg = {ad: round(sum((x["eksik"] or 0) for x in v), 3) for ad, v in gruplar.items()}

        if liste in gruplar:
            satirlar = gruplar[liste]
            toplam = len(satirlar)
            satirlar = satirlar[:limit]
        else:
            kos = "c.miktar IS NULL" if liste == "miktarsiz" else "COALESCE(c.tarih, '') = ''"
            toplam = sayilar[liste]
            satirlar = [dict(r) for r in conn.execute(hareket_sql.format(kosul=kos) + f" LIMIT {limit}", params).fetchall()]
        return jsonify({"liste": liste, "sayilar": sayilar, "kg": kg, "toplam": toplam,
                        "gosterilen": len(satirlar), "satirlar": satirlar, "tipler": HAREKET_TIPLERI})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


# ═════════════════════════════════════════════════
# OTIS ÇIKIŞ EXPORT'U → kalem hareketleri
#   Her satırın AÇIKLAMA-2'sindeki giriş irsaliyesi + SAP kodu ile kalemi bulur,
#   çıkış irsaliyesini (no + tarih + miktar) o kaleme hareket olarak yazar.
#   Hareket tipi GÖNDERİM YERİ'nden gelir (eşleme bir kez seçilir, saklanır).
#   Akış: /oku (dosya → satırlar + önizleme)  →  /onizle (eşleme/seçim değişince)  →  /kaydet
# ═════════════════════════════════════════════════

OTIS_YER_TIPLERI = ("cikis", "a63", "nakil", "tadilat_gidis", "yoksay")
_OC_TOL = 0.01          # kg — girişi aşma / kantar farkı toleransı


def _oc_baslik_norm(s):
    s = str(s or "").strip().upper()
    for a, b in (("İ", "I"), ("I", "I"), ("Ş", "S"), ("Ğ", "G"), ("Ü", "U"), ("Ö", "O"), ("Ç", "C")):
        s = s.replace(a, b)
    return "".join(ch for ch in s if ch.isalnum())


def _oc_yer_varsayilan(yer):
    """Gönderim yeri (+ ' · TRANSFER TİPİ') için önerilen hareket tipi.
    TRANSFER = depolar arası ara bacak: Yeşilovacık/Aspro'ya → boya (tadilat), diğer (90VFA ön stok vb.) → nakil.
    SITE / boş = son teslim → satış/çıkış (Akkuyu adı geçiyorsa nakil)."""
    yer = yer or ""
    ad, _, transfer = yer.partition(" · ")
    y, t = _oc_baslik_norm(ad), _oc_baslik_norm(transfer)
    if t == "TRANSFER":
        return "tadilat_gidis" if ("YESILOVACIK" in y or "ASPRO" in y) else "nakil"
    if t in ("TADILAT", "PRODUCTION", "UNKNOWNPRODUCTION", "TEST"):
        return "tadilat_gidis"      # fason firmaya / tadilata gidiş — geri dönebilir, kesin çıkış değil
    if "AKKUYU" in y:
        return "nakil"
    return "cikis"


def _oc_zincir_coz(conn, satirlar, derinlik=4):
    """Aktarma zinciri: malzeme bir girişten (ör. YPP…0035) ASI irsaliyesiyle Aspro depoya çıkıp boyanıyor,
    sonra OTIS'te 'giriş irsaliyesi = ASI…' diye satılıyor. SAP'de ise hâlâ asıl giriş (YPP) üzerinden takip ediliyor.
    Girişi sistemde OLMAYAN bir satırın giriş no'su, (bu dosyada ya da daha önce saklanan OTIS çıkışlarında) aynı
    malzemenin bir ÇIKIŞ no'suysa, o çıkışın girişine bağlanır; birden fazla kaynak girişse miktar oranla bölünür."""
    bizde = {r[0] for r in conn.execute("SELECT irsaliye_no FROM fason_irsaliye").fetchall()}

    def anahtar(sap, tanim):
        return (tanim or "").strip() or (sap or "").strip()

    kaynak = {}                       # (çıkış no, malzeme) → {giriş no: kg}
    for x in satirlar:
        d = kaynak.setdefault((x["cikis_no"], anahtar(x.get("sap"), x.get("tanim"))), {})
        d[x["giris_no"]] = d.get(x["giris_no"], 0.0) + (x.get("kg") or 0.0)
    gerekli = {x["giris_no"] for x in satirlar if x.get("giris_no") and x["giris_no"] not in bizde}
    if not gerekli:
        return satirlar
    # Bu dosyada olmayan aktarmalar için daha önce saklanan OTIS çıkışlarına bak
    eksik_nolar = [g for g in gerekli if not any(k[0] == g for k in kaynak)]
    for i in range(0, len(eksik_nolar), 900):
        p_ = eksik_nolar[i:i + 900]
        for r in conn.execute(f"SELECT cikis_no, giris_no, sap_kodu, tanim, kg FROM fason_otis_cikis_satir WHERE cikis_no IN ({','.join('?' * len(p_))})", p_).fetchall():
            d = kaynak.setdefault((r[0], anahtar(r[2], r[3])), {})
            d[r[1]] = d.get(r[1], 0.0) + (r[4] or 0.0)

    sonuc = []
    for x in satirlar:
        parcalar = [(x["giris_no"], 1.0, [])]
        for _ in range(derinlik):
            yeni, degisti = [], False
            for g, oran, zincir in parcalar:
                src = kaynak.get((g, anahtar(x.get("sap"), x.get("tanim")))) if g and g not in bizde else None
                src = {k: v for k, v in (src or {}).items() if k and k != g and v > 0}
                if not src:
                    yeni.append((g, oran, zincir))
                    continue
                top = sum(src.values())
                for kg_, v in src.items():
                    yeni.append((kg_, oran * v / top, zincir + [g]))
                degisti = True
            parcalar = yeni
            if not degisti:
                break
        if len(parcalar) == 1 and not parcalar[0][2]:
            sonuc.append(x)
            continue
        for g, oran, zincir in parcalar:
            y = dict(x, giris_no=g, kg=round((x.get("kg") or 0) * oran, 3),
                     kantar=round(x["kantar"] * oran, 3) if x.get("kantar") is not None else None,
                     aktarma=" → ".join(zincir) if zincir else None)
            sonuc.append(y)
    return sonuc


def _oc_yer_tipleri(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT yer, tip FROM fason_gonderim_yeri").fetchall()}


def _oc_tip_bul(yer_tipleri, yer):
    """Kayıtlı eşleme: önce 'yer · transfer tipi'; ara bacak (TRANSFER) değilse eski, sadece yer adıyla
    kaydedilmiş eşleme de geçerli; yoksa önerilen."""
    yer = yer or ""
    if yer in yer_tipleri:
        return yer_tipleri[yer], True
    ad, _, transfer = yer.partition(" · ")
    if transfer and _oc_baslik_norm(transfer) != "TRANSFER" and ad in yer_tipleri:
        return yer_tipleri[ad], True
    return _oc_yer_varsayilan(yer), False


def _otis_kalem_eslestir(ks, sap, tanim):
    """OTIS satırını (OTIS SAP kodu + tanım) bir giriş irsaliyesinin kalemlerine bağlar. Sıra: OTIS SAP kodu →
    OTIS tanımı → SAP kodu → SAP tanımı; bir kural birden fazla kaleme denk gelirse sonrakiyle daraltılır.
    Döner: (eşleşen kalemler, 'otis' / 'sap' / '')"""
    eslesen, etiket_, aday = [], "", ks
    for alan, deger, etiket in (("otis_sap_kodu", sap, "otis"), ("otis_malzeme_tanim", tanim, "otis"),
                                ("sap_kodu", sap, "sap"), ("malzeme_tanim", tanim, "sap")):
        if not deger:
            continue
        alt = [k for k in aday if (k[alan] or "").strip() == deger]
        if len(alt) == 1:
            return alt, etiket
        if alt:
            eslesen, aday, etiket_ = alt, alt, etiket
    return eslesen, etiket_


def _oc_dosya_oku(dosya):
    """OTIS çıkış export'unu okur; aynı (çıkış no, giriş no, SAP, malzeme) satırlarını toplar.
    Döner: (satırlar, istatistik)"""
    from openpyxl import load_workbook
    wb = load_workbook(dosya, data_only=True, read_only=True)
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    try:
        hdr = [_oc_baslik_norm(h) for h in next(it)]
    except StopIteration:
        raise ValueError("Dosya boş")

    def kol(*adlar):
        for a in adlar:
            a = _oc_baslik_norm(a)
            if a in hdr:
                return hdr.index(a)
        return -1

    c_no, c_tarih = kol("İRSALİYE NO"), kol("İRSALİYE TARİHİ")
    c_yer, c_tanim, c_sap = kol("GÖNDERİM YERİ"), kol("MALZEME TANİM", "MALZEME TANIM"), kol("SAP KODU")
    c_kg, c_kantar = kol("MİKTAR(KG)"), kol("MİKTAR(KANTAR)")
    c_transfer = kol("TRANSFER TİPİ")      # TRANSFER = depolar arası ara bacak (Aspro / ön stok), SITE = son teslim
    c_siparis = kol("ÇIKIŞ SİPARİŞ NO", "İMALAT SİPARİŞ NO")   # SAP çıkış Excel'inde "Metin"
    c_alici = kol("TESLİM ALAN")                                # SAP çıkış Excel'inde "Alıcı"
    # Giriş irsaliyesi: yeni export'ta ayrı "GİRİŞ İRSALİYE NO" kolonu var; yoksa AÇIKLAMA-2
    c_giris, c_durum = kol("GİRİŞ İRSALİYE NO", "GİRİŞ İRSALİYESİ", "AÇIKLAMA-2", "ACIKLAMA2"), kol("DURUM")
    eksik = [ad for ad, c in (("İRSALİYE NO", c_no), ("İRSALİYE TARİHİ", c_tarih), ("GÖNDERİM YERİ", c_yer),
                              ("SAP KODU", c_sap), ("MİKTAR(KG)", c_kg), ("GİRİŞ İRSALİYE NO / AÇIKLAMA-2", c_giris)) if c < 0]
    if eksik:
        raise ValueError("Beklenen kolonlar bulunamadı: " + ", ".join(eksik) + " — OTIS çıkış export'u mu?")

    def al(r, i):
        return r[i] if 0 <= i < len(r) else None

    def sayi(v):
        try:
            return _tr_sayi(v)
        except ValueError:
            return None

    gruplar, sira = {}, []
    ist = {"satir": 0, "iptal": 0, "eksik": 0, "tarih_hatali": 0}
    for r in it:
        if not r or all(v is None or str(v).strip() == "" for v in r):
            continue
        ist["satir"] += 1
        durum = _oc_baslik_norm(al(r, c_durum))
        if "IPTAL" in durum:
            ist["iptal"] += 1
            continue
        no = str(al(r, c_no) or "").strip()
        kg = sayi(al(r, c_kg))
        if not no or kg is None:
            ist["eksik"] += 1
            continue
        try:
            tarih = _tarih_iso(al(r, c_tarih))
        except ValueError:
            tarih = None
            ist["tarih_hatali"] += 1
        sap = str(al(r, c_sap) or "").strip()
        if sap.endswith(".0"):
            sap = sap[:-2]
        tanim = str(al(r, c_tanim) or "").strip()
        giris = str(al(r, c_giris) or "").strip()
        yer = str(al(r, c_yer) or "").strip()
        transfer = str(al(r, c_transfer) or "").strip().upper()
        if transfer:
            yer = f"{yer} · {transfer}"     # eşleme yer + transfer tipine göre (aynı yere hem ara transfer hem teslim olabilir)
        kantar = sayi(al(r, c_kantar))
        anahtar = (no, giris, sap, tanim)
        g = gruplar.get(anahtar)
        if not g:
            g = gruplar[anahtar] = {"cikis_no": no, "tarih": tarih, "yer": yer, "giris_no": giris, "sap": sap,
                                    "tanim": tanim, "kg": 0.0, "kantar": 0.0, "kantar_var": False, "satir_sayisi": 0}
            sira.append(anahtar)
        g["kg"] += kg
        if kantar is not None:
            g["kantar"] += kantar
            g["kantar_var"] = True
        g["satir_sayisi"] += 1
        if not g["tarih"] and tarih:
            g["tarih"] = tarih
        if not g.get("siparis"):
            g["siparis"] = str(al(r, c_siparis) or "").strip()
        if not g.get("alici"):
            g["alici"] = str(al(r, c_alici) or "").strip()
    satirlar = []
    for a in sira:
        g = gruplar[a]
        g["kg"] = round(g["kg"], 3)
        g["kantar"] = round(g["kantar"], 3) if g.pop("kantar_var") else None
        satirlar.append(g)
    return satirlar, ist


def _oc_katki(tip, m):
    """Hareketin 'elden çıkan' miktara katkısı (girişi aşma kontrolü için)."""
    m = m or 0
    if tip == "tadilat_donus":
        return -m
    if tip in ("cikis", "a63", "nakil", "tadilat_gidis"):
        return m
    return 0


def _oc_onizle(conn, satirlar, yer_tipleri):
    """Her satırı sınıflandırır. grup:
       yazilacak  — yeni ya da güncellenecek, sorun yok (varsayılan seçili)
       baska      — kalemde bu dosyada olmayan, elle/başka kaynaktan girilmiş hareket var (önizleme, seçili değil)
       fazla      — toplam çıkış girişi aşıyor / giriş miktarı yok (kontrol et, seçili değil)
       kontrol    — kalem bulunamadı / birden fazla aday / giriş no boş (seçim gerekli)
       giris_yok  — AÇIKLAMA-2'deki giriş irsaliyesi bizde yok (bekleyene alınır)
       ayni       — zaten aynen kayıtlı
       yoksay     — gönderim yeri 'yoksay' olarak işaretli"""
    irs_cache, kalem_cache, hareket_cache, kalem_bilgi = {}, {}, {}, {}

    def irsaliyeler(no):
        if no not in irs_cache:
            irs_cache[no] = [r[0] for r in conn.execute("SELECT id FROM fason_irsaliye WHERE irsaliye_no = ?", (no,)).fetchall()]
        return irs_cache[no]

    def giris_no_coz(ham):
        """AÇIKLAMA-2'de birden fazla değer / ek metin olabilir — bizde olan ilk numarayı al."""
        import re as _re
        if not ham:
            return "", []
        if irsaliyeler(ham):
            return ham, irsaliyeler(ham)
        for t in _re.split(r"[\s,;/|]+", ham):
            t = t.strip()
            if t and t != ham and irsaliyeler(t):
                return t, irsaliyeler(t)
        takma = _giris_takma_no(ham)          # SAP'de IRS ile açılmış olabilir (MMK…030 ↔ IRS…030)
        if takma and irsaliyeler(takma):
            return takma, irsaliyeler(takma)
        return ham, []

    def kalemler(irs_idler):
        anahtar = tuple(irs_idler)
        if anahtar not in kalem_cache:
            kalem_cache[anahtar] = [dict(r) for r in conn.execute(
                f"SELECT id, malzeme_tanim, sap_kodu, otis_sap_kodu, otis_malzeme_tanim FROM fason_kalem WHERE irsaliye_id IN ({','.join('?' * len(irs_idler))})",
                list(irs_idler)).fetchall()]
        return kalem_cache[anahtar]

    def bilgi(kid):
        if kid not in kalem_bilgi:
            r = conn.execute("""
                SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.otis_giris, k.durum, NULLIF(k.belge_tarihi, '') AS giris_tarihi,
                       i.irsaliye_no, COALESCE(f.ad, 'Firma Yok') AS firma
                FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
                LEFT JOIN fason_firma f ON f.id = i.firma_id WHERE k.id = ?""", (kid,)).fetchone()
            kalem_bilgi[kid] = dict(r) if r else None
        return kalem_bilgi[kid]

    def hareketler(kid):
        if kid not in hareket_cache:
            hareket_cache[kid] = [dict(r) for r in conn.execute(
                "SELECT id, irsaliye_no, miktar, tarih, COALESCE(tip, 'cikis') AS tip, kaynak FROM fason_kalem_cikis WHERE kalem_id = ? ORDER BY COALESCE(tarih, ''), id",
                (kid,)).fetchall()]
        return hareket_cache[kid]

    def aday_ozet(kid):
        b = bilgi(kid)
        return {"id": kid, "malzeme_tanim": b["malzeme_tanim"], "sap_kodu": b["sap_kodu"] or "",
                "irsaliye_no": b["irsaliye_no"], "firma": b["firma"], "giris": b["giris_miktari"]} if b else None

    sonuc = []
    for idx, s in enumerate(satirlar):
        tip = _oc_tip_bul(yer_tipleri, s.get("yer"))[0]
        r = {"idx": idx, "cikis_no": s["cikis_no"], "tarih": s.get("tarih"), "yer": s.get("yer") or "",
             "giris_no": s.get("giris_no") or "", "giris_ham": s.get("giris_no") or "", "aktarma": s.get("aktarma"),
             "sap": s.get("sap") or "", "tanim": s.get("tanim") or "",
             "kg": s.get("kg"), "kantar": s.get("kantar"), "satir_sayisi": s.get("satir_sayisi") or 1,
             "tip": tip, "kalem": None, "adaylar": [], "neden": "", "uyarilar": [], "eski": None, "baska": []}
        if r["aktarma"]:
            r["uyarilar"].append(f"Aktarma üzerinden: {r['aktarma']} → {r['giris_no']} girişine bağlandı")
        r["kantar_farki"] = (r["kantar"] is not None and r["kg"] is not None and abs(r["kantar"] - r["kg"]) > _OC_TOL)
        if r["kantar_farki"]:
            r["uyarilar"].append(f"Kantar {_tr_sayi_yaz(r['kantar'])} ≠ KG {_tr_sayi_yaz(r['kg'])}")
        sonuc.append(r)
        if tip == "yoksay":
            r["grup"], r["neden"] = "yoksay", "Gönderim yeri yok sayılıyor"
            continue
        if not r["tarih"]:
            r["uyarilar"].append("İrsaliye tarihi okunamadı")

        secilen = s.get("kalem_id")
        if secilen:                                   # kullanıcı kontrol listesinden kalemi seçti
            try:
                secilen = int(secilen)
            except (TypeError, ValueError):
                secilen = None
        if secilen and bilgi(secilen):
            r["kalem_id"] = secilen
            r["elle_secildi"] = True
            continue

        giris_no, irs_idler = giris_no_coz(r["giris_no"])
        r["giris_no"] = giris_no
        if not giris_no:
            # Giriş no yazılmamış — SAP koduyla tüm kalemlerde aday ara
            adaylar = [x[0] for x in conn.execute(
                "SELECT id FROM fason_kalem WHERE sap_kodu = ? AND ? <> '' LIMIT 20", (r["sap"], r["sap"])).fetchall()]
            r["grup"], r["neden"] = "kontrol", "AÇIKLAMA-2'de giriş irsaliyesi yok"
            r["adaylar"] = [a for a in (aday_ozet(k) for k in adaylar) if a]
            continue
        if not irs_idler:
            r["grup"], r["neden"] = "giris_yok", "Giriş irsaliyesi sistemde yok"
            continue
        ks = kalemler(irs_idler)
        # Eşleştirme sırası: 1) kalemin bağlı olduğu OTIS kaydı (OTIS SAP kodu / OTIS tanımı — "Eşleştirme
        # Bekleyenler"de elle yapılan eşleştirmeler dahil; OTIS çıkış export'u OTIS'in kodunu taşır)
        # 2) SAP kodu  3) SAP malzeme tanımı
        # Bir kural birden fazla kaleme denk gelirse (ör. OTIS'te ortak 'GEÇİCİ..' kodu) sonraki kurallarla daraltılır.
        eslesen, r["eslesme"] = _otis_kalem_eslestir(ks, r["sap"], r["tanim"])
        if len(eslesen) == 1:
            r["kalem_id"] = eslesen[0]["id"]
        elif not eslesen:
            r["grup"], r["neden"] = "kontrol", "Giriş irsaliyesinde bu SAP kodu / malzeme yok"
            r["adaylar"] = [a for a in (aday_ozet(k["id"]) for k in ks[:300]) if a]
        else:
            r["grup"], r["neden"] = "kontrol", f"Giriş irsaliyesinde {len(eslesen)} aday kalem var"
            r["adaylar"] = [a for a in (aday_ozet(k["id"]) for k in eslesen[:50]) if a]

    # Kalem bazında: bu dosyanın getirdiği toplam + dosyada olmayan mevcut hareketler girişi aşıyor mu?
    kalem_satirlari = {}
    for r in sonuc:
        if r.get("kalem_id"):
            kalem_satirlari.setdefault(r["kalem_id"], []).append(r)
    for kid, rs in kalem_satirlari.items():
        b = bilgi(kid)
        hs = hareketler(kid)
        # OTIS çıkışı OTIS (kantar) kilosuyla gelir; hareketler SAP miktarıyla tutulur. Kantar farkı varsa
        # oranla SAP ölçeğine çevir (OTIS kg × SAP giriş / OTIS giriş).
        oran = None
        if b["giris_miktari"] and b["otis_giris"] and abs(b["giris_miktari"] - b["otis_giris"]) > _OC_TOL:
            oran = b["giris_miktari"] / b["otis_giris"]
        for x in rs:
            if "kg_otis" not in x:
                x["kg_otis"] = x["kg"]
                if oran and x["kg"] is not None:
                    x["kg"] = round(x["kg"] * oran, 3)
                    x["uyarilar"].append(f"Kantar farkı: OTIS {_tr_sayi_yaz(x['kg_otis'])} kg → SAP ölçeğinde {_tr_sayi_yaz(x['kg'])} kg yazılır")
                    # MİKTAR(KANTAR) zaten SAP ölçeğindeki miktarla tutuyorsa ayrıca "kantar farkı" uyarısı gösterme
                    if x.get("kantar_farki") and x["kantar"] is not None and abs(x["kantar"] - x["kg"]) <= _OC_TOL:
                        x["kantar_farki"] = False
                        x["uyarilar"] = [u for u in x["uyarilar"] if not u.startswith("Kantar ") or u.startswith("Kantar farkı")]
        dosya_nolari = {x["cikis_no"] for x in rs}
        disarida = [h for h in hs if h["irsaliye_no"] not in dosya_nolari]
        # Girişi aşma: kesin çıkış > giriş, ya da (dönmemiş tadilat + nakil) > giriş. Tadilata/Aspro'ya gidip
        # oradan satılan malzeme çift sayılmaz (satış, tadilattaki miktarı kapatır).
        top = {"kesin": 0.0, "nakil": 0.0, "gidis": 0.0, "donus": 0.0}
        for tip_, m_ in [(h["tip"], h["miktar"]) for h in disarida] + [(x["tip"], x["kg"]) for x in rs]:
            alan = "kesin" if tip_ in KESIN_CIKIS_TIPLERI else {"nakil": "nakil", "tadilat_gidis": "gidis", "tadilat_donus": "donus"}.get(tip_)
            if alan:
                top[alan] += m_ or 0.0
        disarida_gonderilen = max(0.0, top["gidis"] - top["donus"]) + top["nakil"]
        giris = b["giris_miktari"]
        fazla_neden = ""
        if giris is None:
            fazla_neden = "Kalemin SAP giriş miktarı yok"
        elif top["kesin"] > giris + _OC_TOL:
            fazla_neden = (f"Toplam kesin çıkış {_tr_sayi_yaz(round(top['kesin'], 3))} kg > giriş {_tr_sayi_yaz(giris)} kg"
                           + (f" (dosyada olmayan hareketler dahil)" if disarida else ""))
        elif disarida_gonderilen > giris + _OC_TOL:
            fazla_neden = (f"Tadilat/Aspro + Akkuyu'ya gönderilen {_tr_sayi_yaz(round(disarida_gonderilen, 3))} kg > giriş {_tr_sayi_yaz(giris)} kg")
        baska = [{"no": h["irsaliye_no"], "miktar": h["miktar"], "tarih": h["tarih"], "tip": h["tip"], "kaynak": h["kaynak"]}
                 for h in disarida if (h["kaynak"] or "") not in ("otis", "sap")]
        for x in rs:
            x["kalem"] = {"id": kid, "malzeme_tanim": b["malzeme_tanim"], "sap_kodu": b["sap_kodu"] or "",
                          "giris": giris, "durum": b["durum"] or "", "giris_tarihi": b["giris_tarihi"],
                          "irsaliye_no": b["irsaliye_no"], "firma": b["firma"]}
            x["baska"] = baska
            if x["tarih"] and b["giris_tarihi"] and x["tarih"] < b["giris_tarihi"][:10]:
                x["uyarilar"].append("Çıkış tarihi girişten önce")
            var = next((h for h in hs if h["irsaliye_no"] == x["cikis_no"]), None)
            if var:
                x["eski"] = {"miktar": var["miktar"], "tarih": var["tarih"], "tip": var["tip"], "kaynak": var["kaynak"]}
                ayni = (var["miktar"] is not None and abs(var["miktar"] - x["kg"]) < 0.001
                        and (var["tarih"] or "") == (x["tarih"] or "") and var["tip"] == x["tip"])
                x["islem"] = "ayni" if ayni else "guncelle"
            else:
                x["islem"] = "yeni"
            if x["islem"] == "ayni":
                x["grup"], x["neden"] = "ayni", "Zaten aynen kayıtlı"
            elif fazla_neden:
                x["grup"], x["neden"] = "fazla", fazla_neden
            elif baska:
                x["grup"], x["neden"] = "baska", "Kalemde elle/başka kaynaktan girilmiş hareket var"
            elif x["islem"] == "guncelle" and (x["eski"]["kaynak"] or "") not in ("otis", "aktarim", "sap"):
                x["grup"] = "baska"
                x["neden"] = (f"Elle girilmiş kayıt güncellenecek: {_tr_sayi_yaz(x['eski']['miktar']) or 'miktar yok'} kg"
                              f" → {_tr_sayi_yaz(x['kg'])} kg")
            else:
                x["grup"] = "yazilacak"
    # SAP çıkış verisi yüklüyse: bu çıkış SAP'de de yapılmış mı?
    kapsam = _sap_kapsam(conn)
    sap = _sap_toplamlari(conn, {r["cikis_no"] for r in sonuc if r.get("kalem")}) if kapsam["satir"] else {}
    sap_nolari = {no for (_, no) in sap.keys()}
    otis_top = _otis_cikis_toplamlari(conn, sap_nolari) if sap else {}
    for r in sonuc:
        r.setdefault("islem", None)
        r["sap_durum"], r["sap_miktar"] = None, None
        k = r.get("kalem")
        # Nakil (Akkuyu) ve tadilat işlemleri SAP'de yapılmıyor — SAP'de aranmaz
        if not k or not kapsam["satir"] or r["grup"] == "yoksay" or r["tip"] not in KESIN_CIKIS_TIPLERI:
            continue
        s_ = sap.get((k["sap_kodu"], r["cikis_no"]))
        kapsamda = _sap_kapsamda(kapsam, r["tarih"]) or r["cikis_no"] in sap_nolari
        a_ = (k["sap_kodu"], r["cikis_no"])
        r["otis_toplam"] = _otis_ref(otis_top.get(a_), s_["miktar"] if s_ else None)
        r["sap_durum"] = _sap_durum(r["kg"], s_, kapsamda, otis_top.get(a_))
        r["sap_miktar"] = s_["miktar"] if s_ else None
    return sonuc


def _oc_ozet(satirlar):
    gruplar = {}
    for r in satirlar:
        g = gruplar.setdefault(r["grup"], {"adet": 0, "kg": 0.0})
        g["adet"] += 1
        g["kg"] += r["kg"] or 0
    for g in gruplar.values():
        g["kg"] = round(g["kg"], 2)
    kantar = [r for r in satirlar if r.get("kantar_farki")]
    sap_eksik = [r for r in satirlar if r.get("sap_durum") in ("sap_yok", "miktar_farki")]
    return {"gruplar": gruplar, "kantar_farki": len(kantar), "sap_eksik": len(sap_eksik),
            "sap_yuklu": any(r.get("sap_durum") for r in satirlar)}


def _oc_yerler(satirlar, kayitli):
    yerler = {}
    for s in satirlar:
        y = s.get("yer") or ""
        if y not in yerler:
            tip_, kayitli_mi = _oc_tip_bul(kayitli, y)
            yerler[y] = {"yer": y, "adet": 0, "kg": 0.0, "tip": tip_, "yeni": not kayitli_mi}
        d = yerler[y]
        d["adet"] += 1
        d["kg"] += s.get("kg") or 0
    return sorted(({**v, "kg": round(v["kg"], 2)} for v in yerler.values()), key=lambda v: -v["adet"])


def _oc_yetki():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if not _fason_yetki_var_mi("fason_kalem_guncelle"):
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403
    return None


# Okunan OTIS çıkış dosyaları sunucuda tutulur (büyük export'larda — 60 bin+ satır — tüm satırları
# tarayıcıya gidip gelmek onlarca MB ediyor). Tarayıcı sadece token + seçimleri gönderir.
_OC_ONBELLEK = {}
_OC_ONBELLEK_SURE = 2 * 3600
_OC_EKRAN_SINIR = 300        # "Giriş bizde yok" / "Zaten kayıtlı" sekmelerinde ekrana gönderilen en fazla satır


def _oc_onbellek_koy(ham, dosya):
    import uuid, time as _t
    simdi = _t.time()
    for k in [k for k, v in _OC_ONBELLEK.items() if simdi - v["zaman"] > _OC_ONBELLEK_SURE]:
        _OC_ONBELLEK.pop(k, None)
    while len(_OC_ONBELLEK) >= 4:
        _OC_ONBELLEK.pop(min(_OC_ONBELLEK, key=lambda k: _OC_ONBELLEK[k]["zaman"]))
    token = uuid.uuid4().hex
    _OC_ONBELLEK[token] = {"ham": ham, "dosya": dosya, "zaman": simdi, "kullanici": session.get("kullanici", "")}
    return token


def _oc_onbellek_al(token):
    import time as _t
    v = _OC_ONBELLEK.get(str(token or ""))
    if not v:
        return None
    v["zaman"] = _t.time()
    return v


def _oc_ham_secimli(ham, secimler):
    """Kullanıcının 'Kalem seç' sekmesinde seçtiği kalemleri (idx → kalem_id) ham satırlara uygular."""
    if not secimler:
        return ham
    sonuc = []
    for i, h in enumerate(ham):
        kid = secimler.get(str(i))
        sonuc.append(dict(h, kalem_id=kid) if kid else h)
    return sonuc


def _oc_cevap(token, ham, kayitli, onizleme, dosya, ist=None):
    """Önizleme cevabı: işlem gerektiren satırların hepsi; 'giriş bizde yok' ve 'zaten kayıtlı'dan sadece ilk N."""
    sayac, gosterilen = {"giris_yok": 0, "ayni": 0}, []
    for r in onizleme:
        if r["grup"] in sayac:
            sayac[r["grup"]] += 1
            if sayac[r["grup"]] > _OC_EKRAN_SINIR:
                continue
        # Boş alanları gönderme (büyük dosyada cevabı küçültür; ekran eksik alanı boş sayar)
        gosterilen.append({k: v for k, v in r.items() if v not in (None, "", [], False) or k in ("idx", "grup")})
    ozet = _oc_ozet(onizleme)
    ozet["kisaltilan"] = {g: n for g, n in sayac.items() if n > _OC_EKRAN_SINIR}
    return jsonify({"durum": "ok", "token": token, "istatistik": ist, "yerler": _oc_yerler(ham, kayitli),
                    "satirlar": gosterilen, "ozet": ozet, "tipler": HAREKET_TIPLERI, "dosya": dosya,
                    "satir_sayisi": len(ham)})


@fason_bp.route("/api/fason/otis-cikis/oku", methods=["POST"])
def api_fason_otis_cikis_oku():
    """OTIS çıkış export'unu okur, gruplar, ham satırları (giriş bazında) saklar ve önizleme döner.
    Hareketlere dokunmaz — onlar Kaydet ile yazılır."""
    hata = _oc_yetki()
    if hata:
        return hata
    dosya = request.files.get("dosya")
    if not dosya or not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya seçilmedi"}), 400
    try:
        satirlar, ist = _oc_dosya_oku(dosya)
    except ValueError as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 400
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": f"Dosya okunamadı: {e}"}), 400
    if not satirlar:
        return jsonify({"durum": "hata", "mesaj": "Dosyada işlenecek satır yok"}), 400
    conn = get_db()
    try:
        kayitli = _oc_yer_tipleri(conn)
        # Ham OTIS çıkış satırları (giriş bazında, eşleşsin eşleşmesin hepsi) — SAP karşılaştırması ve
        # "kayıtlı OTIS çıkışından tekrar dene" için saklanır. Hareketlere sen Kaydet'e basmadan dokunulmaz.
        satirlar = _oc_zincir_coz(conn, satirlar)       # ASI (Aspro aktarma) üzerinden satışları asıl girişe bağla
        _otis_cikis_satir_kaydet(conn, satirlar)
        conn.commit()
        token = _oc_onbellek_koy(satirlar, dosya.filename)
        return _oc_cevap(token, satirlar, kayitli, _oc_onizle(conn, satirlar, kayitli), dosya.filename, ist)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-cikis/onizle", methods=["POST"])
def api_fason_otis_cikis_onizle():
    """Gönderim yeri eşlemesi ya da 'Kalem seç' değişince önizlemeyi yeniden hesaplar.
    Body: {token, yer_tipleri: {yer: tip}, secimler: {idx: kalem_id}}. DB'ye yazmaz."""
    hata = _oc_yetki()
    if hata:
        return hata
    d = request.get_json() or {}
    on = _oc_onbellek_al(d.get("token"))
    if not on:
        return jsonify({"durum": "hata", "mesaj": "Önizlemenin süresi doldu — dosyayı tekrar oku"}), 410
    gelen = {str(k): v for k, v in (d.get("yer_tipleri") or {}).items() if v in OTIS_YER_TIPLERI}
    conn = get_db()
    try:
        kayitli = _oc_yer_tipleri(conn)
        kayitli.update(gelen)
        ham = _oc_ham_secimli(on["ham"], d.get("secimler") or {})
        return _oc_cevap(d.get("token"), on["ham"], kayitli, _oc_onizle(conn, ham, kayitli), on["dosya"])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/otis-cikis/kaydet", methods=["POST"])
def api_fason_otis_cikis_kaydet():
    """Seçilen önizleme satırlarını hareket olarak yazar.
    Body: {token, idxler: [satır idx], yer_tipleri: {yer: tip}, secimler: {idx: kalem_id}}
    Önizleme sunucuda yeniden hesaplanır (ekranla aynı kurallar); sadece seçilen ve yazılabilir satırlar yazılır.
    Kalemde aynı çıkış no zaten varsa yeni kayıt açılmaz; miktar/tarih/tip güncellenir."""
    hata = _oc_yetki()
    if hata:
        return hata
    d = request.get_json() or {}
    on = _oc_onbellek_al(d.get("token"))
    if not on:
        return jsonify({"durum": "hata", "mesaj": "Önizlemenin süresi doldu — dosyayı tekrar oku"}), 410
    kullanici = session.get("kullanici", "")
    yer_tipleri = {str(k): v for k, v in (d.get("yer_tipleri") or {}).items() if v in OTIS_YER_TIPLERI}
    try:
        idxler = {int(x) for x in (d.get("idxler") or [])}
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "Geçersiz seçim"}), 400
    if not idxler and not yer_tipleri:
        return jsonify({"durum": "hata", "mesaj": "Kaydedilecek satır seçilmedi"}), 400

    conn = get_db()
    try:
        for yer, tip in yer_tipleri.items():
            conn.execute("""
                INSERT INTO fason_gonderim_yeri (yer, tip, guncelleyen, guncelleme) VALUES (?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(yer) DO UPDATE SET tip = excluded.tip, guncelleyen = excluded.guncelleyen, guncelleme = excluded.guncelleme
            """, (yer, tip, kullanici))
        kayitli = _oc_yer_tipleri(conn)
        kayitli.update(yer_tipleri)
        onizleme = _oc_onizle(conn, _oc_ham_secimli(on["ham"], d.get("secimler") or {}), kayitli)

        temiz = {}
        for r in onizleme:
            if r["idx"] not in idxler or r["grup"] not in ("yazilacak", "baska", "fazla") or not r.get("kalem"):
                continue
            if not r["kg"] or r["kg"] <= 0 or r["tip"] not in HAREKET_TIPLERI or r["tip"] == "tadilat_donus":
                continue
            a = (r["kalem"]["id"], r["cikis_no"])
            if a in temiz:                       # aynı kaleme aynı çıkıştan iki grup düştüyse topla
                temiz[a]["miktar"] = round(temiz[a]["miktar"] + r["kg"], 3)
            else:
                temiz[a] = {"kalem_id": a[0], "no": a[1], "miktar": round(r["kg"], 3), "tarih": r["tarih"], "tip": r["tip"]}

        eklenen = guncellenen = 0
        kalem_son_tip = {}
        for s_ in temiz.values():
            var = conn.execute("SELECT id FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?",
                               (s_["kalem_id"], s_["no"])).fetchone()
            if var:
                conn.execute("UPDATE fason_kalem_cikis SET miktar = ?, tarih = ?, tip = ?, kaynak = 'otis' WHERE id = ?",
                             (s_["miktar"], s_["tarih"], s_["tip"], var[0]))
                guncellenen += 1
            else:
                conn.execute("""
                    INSERT INTO fason_kalem_cikis (kalem_id, irsaliye_no, miktar, tarih, kaynak, ekleyen, tip)
                    VALUES (?, ?, ?, ?, 'otis', ?, ?)
                """, (s_["kalem_id"], s_["no"], s_["miktar"], s_["tarih"], kullanici, s_["tip"]))
                eklenen += 1
            onceki = kalem_son_tip.get(s_["kalem_id"])
            if not onceki or (s_["tarih"] or "") >= onceki[0]:
                kalem_son_tip[s_["kalem_id"]] = (s_["tarih"] or "", s_["tip"])

        durum_degisen = 0
        for kid, (_, tip) in kalem_son_tip.items():
            _cikis_ozet_guncelle(conn, kid)
            if _hareket_sonrasi_durum(conn, kid, tip):
                durum_degisen += 1
        conn.commit()
        giris_yok = sum(1 for r in onizleme if r["grup"] == "giris_yok")
        log_kaydet("OTIS Çıkış Import",
                   f"{eklenen} hareket eklendi, {guncellenen} güncellendi, {durum_degisen} kalemin durumu değişti", None, on["dosya"])
        mesaj = f"{eklenen} hareket eklendi" + (f", {guncellenen} güncellendi" if guncellenen else "")
        if durum_degisen:
            mesaj += f" · {durum_degisen} kalemin durumu güncellendi"
        if giris_yok:
            mesaj += (f" · {giris_yok} satırın giriş irsaliyesi sistemde yok — saklandı, giriş eklenince "
                      "'Kayıtlı OTIS çıkışından tekrar dene' ile bağlanır")
        return jsonify({"durum": "ok", "eklenen": eklenen, "guncellenen": guncellenen, "durum_degisen": durum_degisen,
                        "mesaj": mesaj})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


_OC_BEKLEYEN_SQL = """
    FROM fason_otis_cikis_satir s
    WHERE s.giris_no IN (SELECT irsaliye_no FROM fason_irsaliye)
      AND NOT EXISTS (SELECT 1 FROM fason_kalem_cikis c
                      JOIN fason_kalem k ON k.id = c.kalem_id
                      JOIN fason_irsaliye i ON i.id = k.irsaliye_id
                      WHERE c.irsaliye_no = s.cikis_no AND i.irsaliye_no = s.giris_no)
"""


@fason_bp.route("/api/fason/otis-cikis/bekleyenler", methods=["GET"])
def api_fason_otis_cikis_bekleyenler():
    """Saklanan OTIS çıkış satırlarından: giriş irsaliyesi ŞU AN sistemde olup bu çıkışı henüz o girişe yazılmamış olanlar.
    (Giriş sonradan ZMM068 ile eklendiyse dosyayı tekrar yüklemeden bağlanır.) ?sadece_sayi=1 → sadece adet."""
    hata = _oc_yetki()
    if hata:
        return hata
    conn = get_db()
    try:
        if request.args.get("sadece_sayi"):
            return jsonify({"durum": "ok", "adet": conn.execute("SELECT COUNT(*) " + _OC_BEKLEYEN_SQL).fetchone()[0]})
        ham = [{"cikis_no": r["cikis_no"], "tarih": r["tarih"], "yer": r["yer"] or "", "giris_no": r["giris_no"] or "",
                "sap": r["sap_kodu"] or "", "tanim": r["tanim"] or "", "kg": r["kg"], "kantar": None, "satir_sayisi": 1}
               for r in conn.execute("SELECT s.* " + _OC_BEKLEYEN_SQL + " ORDER BY s.tarih, s.cikis_no, s.id").fetchall()]
        if not ham:
            return jsonify({"durum": "ok", "adet": 0})
        kayitli = _oc_yer_tipleri(conn)
        token = _oc_onbellek_koy(ham, "Kayıtlı OTIS çıkışları")
        d = _oc_cevap(token, ham, kayitli, _oc_onizle(conn, ham, kayitli), "Kayıtlı OTIS çıkışları").get_json()
        d["adet"] = len(ham)
        return jsonify(d)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


# ═════════════════════════════════════════════════
# SAP ÇIKIŞ (çıkış ZMM068'i) ↔ sistem hareketleri karşılaştırması
#   Anahtar: (SAP kodu, çıkış irsaliye no = ZMM068 'Referans').
#   "SAP'de yok"  : sistemde (OTIS'ten / elle) çıkış hareketi var, SAP'de bu irsaliyeyle çıkış yok
#   "Sistemde yok": SAP'de çıkış var, sistemde bu kaleme bu irsaliyeyle hareket yok
#   "Miktar farkı": ikisinde de var, miktar tutmuyor
# ═════════════════════════════════════════════════

_SAP_TOL = 0.01


_SAP_TOL_ORAN = 0.02    # SAP ↔ OTIS toplam karşılaştırmasında %2 (en az _SAP_TOL_KG) tolerans (kantar yuvarlamaları)
_SAP_TOL_KG = 0.5


def _otis_cikis_satir_kaydet(conn, satirlar):
    """OTIS çıkış dosyasının (gruplanmış) satırlarını ham olarak saklar. Dosyada geçen her çıkış no'nun eski
    satırları silinip yenileri yazılır (aynı export tekrar yüklenirse mükerrer olmaz)."""
    nolar = sorted({s["cikis_no"] for s in satirlar if s.get("cikis_no")})
    for i in range(0, len(nolar), 900):
        p_ = nolar[i:i + 900]
        conn.execute(f"DELETE FROM fason_otis_cikis_satir WHERE cikis_no IN ({','.join('?' * len(p_))})", p_)
    conn.executemany("""INSERT INTO fason_otis_cikis_satir (cikis_no, giris_no, sap_kodu, tanim, kg, tarih, yer, siparis, alici)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     [(s["cikis_no"], s.get("giris_no") or "", s.get("sap") or "", s.get("tanim") or "",
                       s.get("kg"), s.get("tarih"), s.get("yer") or "", s.get("siparis") or "", s.get("alici") or "")
                      for s in satirlar if s.get("cikis_no")])


def _otis_sap_haritasi(conn):
    """OTIS satırını SAP koduna bağlamak için haritalar: OTIS SAP kodu / OTIS tanımı / SAP tanımı → SAP kodu.
    Birden fazla SAP koduna giden (belirsiz) anahtarlar haritaya alınmaz."""
    haritalar = ({}, {}, {})
    sapset = set()
    for r in conn.execute("SELECT sap_kodu, otis_sap_kodu, otis_malzeme_tanim, malzeme_tanim FROM fason_kalem WHERE COALESCE(sap_kodu, '') <> ''").fetchall():
        sapset.add(r[0])
        for h, anahtar in zip(haritalar, (r[1], r[2], r[3])):
            anahtar = (anahtar or "").strip()
            if not anahtar:
                continue
            if h.get(anahtar, r[0]) != r[0]:
                h[anahtar] = None          # belirsiz
            else:
                h[anahtar] = r[0]
    return sapset, haritalar


def _giris_takma_no(no):
    """OTIS giriş no → SAP'deki karşılığı. SAP'de giriş bazen firma öneki yerine IRS ile açılıyor:
    OTIS MMK2026000000030 / INC2026000000030 / TOR2026000000176 ↔ SAP IRS2026000000030 ... (aynı yıl + sıra)."""
    import re as _re
    no = (no or "").strip()
    m = _re.match(r"^[A-Z0-9]{3}(\d{13})$", no)
    if not m or no.startswith("IRS"):
        return None
    return "IRS" + m.group(1)


def _otis_giris_kalemleri(conn):
    """OTIS giriş no → (bizdeki irsaliye kalemleri). Önce birebir no, yoksa IRS karşılığı (_giris_takma_no)."""
    irs = {}
    for r in conn.execute("SELECT irsaliye_id, id, malzeme_tanim, sap_kodu, otis_sap_kodu, otis_malzeme_tanim, giris_miktari, otis_giris "
                          "FROM fason_kalem").fetchall():
        irs.setdefault(r["irsaliye_id"], []).append(dict(r))
    nolar = {}
    for r in conn.execute("SELECT id, irsaliye_no FROM fason_irsaliye").fetchall():
        nolar.setdefault(r[1], []).extend(irs.get(r[0], []))
    onbellek = {}

    def bul(giris_no):
        if giris_no not in onbellek:
            ks = nolar.get(giris_no)
            if not ks:
                t = _giris_takma_no(giris_no)
                ks = nolar.get(t) if t else None
            onbellek[giris_no] = ks or []
        return onbellek[giris_no]
    return bul


def _otis_cikis_toplamlari(conn, nolar=None):
    """(SAP kodu, çıkış no) → OTIS toplamları: (bizdeki girişlerin OTIS kg'si, aynısı SAP ölçeğinde, tüm girişler OTIS kg).
    OTIS satırı giriş irsaliyesi üzerinden BİZİM kaleme bağlanır ve bizim SAP kodumuzla toplanır (OTIS'teki SAP kodu
    sadece eşleşme için). Giriş irsaliyesi bizde olmayan satırlar sadece 'tüm girişler' toplamına girer.
    SAP çıkışı bazen OTIS (kantar) kilosuyla, bazen SAP kilosuyla yapılıyor — karşılaştırmada SAP'ye en yakın olan alınır."""
    if nolar is not None:
        nolar = list(nolar)
        if not nolar:
            return {}
        rows = []
        for i in range(0, len(nolar), 900):
            p_ = nolar[i:i + 900]
            rows += conn.execute(f"SELECT cikis_no, giris_no, sap_kodu, tanim, kg FROM fason_otis_cikis_satir WHERE cikis_no IN ({','.join('?' * len(p_))})", p_).fetchall()
    else:
        rows = conn.execute("SELECT cikis_no, giris_no, sap_kodu, tanim, kg FROM fason_otis_cikis_satir").fetchall()
    if not rows:
        return {}
    sapset, (h_otis_sap, h_otis_tanim, h_tanim) = _otis_sap_haritasi(conn)
    giris_kalemleri = _otis_giris_kalemleri(conn)
    esles_onb = {}
    t = {}
    for no, giris, sap, tanim, kg in rows:
        sap, tanim, kg = (sap or "").strip(), (tanim or "").strip(), (kg or 0.0)
        ks = giris_kalemleri(giris)
        kalem = None
        if ks:
            a_ = (giris, sap, tanim)
            if a_ not in esles_onb:
                e_, _ = _otis_kalem_eslestir(ks, sap, tanim)
                esles_onb[a_] = e_[0] if len(e_) == 1 and e_[0]["sap_kodu"] else None
            kalem = esles_onb[a_]
        if kalem:
            d = t.setdefault((kalem["sap_kodu"], no), [0.0, 0.0, 0.0])
            d[0] += kg
            og, sg = kalem["otis_giris"] or 0.0, kalem["giris_miktari"] or 0.0
            d[1] += kg * (sg / og) if og > 0 and sg > 0 else kg
            d[2] += kg
        else:
            anahtar = sap if sap in sapset else (h_otis_sap.get(sap) or h_otis_tanim.get(tanim) or h_tanim.get(tanim))
            if anahtar:
                t.setdefault((anahtar, no), [0.0, 0.0, 0.0])[2] += kg
    sonuc = {}
    for a, v in t.items():
        if v[0] > 0:
            sonuc[a] = (round(v[0], 3), round(v[1], 3), round(v[2], 3))
        else:
            sonuc[a] = (round(v[2], 3),)
    return sonuc


def _otis_ref(otis, sap_m):
    """OTIS toplamı (ham kantar kg, SAP ölçeğine çevrilmiş) içinden SAP miktarına en yakın olanı döner."""
    if otis is None:
        return None
    if not isinstance(otis, (tuple, list)):
        return otis
    if sap_m is None:
        return otis[1] if len(otis) > 1 else otis[0]
    return min(otis, key=lambda v: abs(v - sap_m))


def _sap_kapsam(conn):
    """Yüklü SAP çıkış verisi: satır sayısı, en eski/yeni tarih ve birleştirilmiş kapsam aralıkları."""
    r = conn.execute("SELECT MIN(belge_tarihi), MAX(belge_tarihi), COUNT(*), MAX(yukleme) FROM fason_sap_cikis").fetchone()
    araliklar = []
    for b_, e_ in conn.execute("SELECT bas, bit FROM fason_sap_yukleme ORDER BY bas, bit").fetchall():
        if araliklar and b_ <= _gun_sonra(araliklar[-1][1]):
            if e_ > araliklar[-1][1]:
                araliklar[-1][1] = e_
        else:
            araliklar.append([b_, e_])
    return {"bas": r[0], "bit": r[1], "satir": r[2] or 0, "son_yukleme": r[3], "araliklar": araliklar}


def _gun_sonra(iso):
    try:
        from datetime import timedelta
        return (datetime.strptime(iso[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return iso


def _sap_kapsamda(kapsam, tarih):
    if not tarih:
        return False
    t = tarih[:10]
    return any(b <= t <= e for b, e in kapsam.get("araliklar") or [])


def _sap_toplamlari(conn, nolar=None):
    """(sap_kodu, cikis_no) → {miktar (net), tarih, belgeler, islem_turleri}"""
    if nolar is not None:
        nolar = list(nolar)
        if not nolar:
            return {}
        rows = []
        for i in range(0, len(nolar), 900):
            parca = nolar[i:i + 900]
            rows += conn.execute(
                f"SELECT sap_kodu, cikis_no, miktar, belge_tarihi, malzeme_belgesi, islem_turu, malzeme_tanim FROM fason_sap_cikis WHERE cikis_no IN ({','.join('?' * len(parca))})",
                parca).fetchall()
    else:
        rows = conn.execute("SELECT sap_kodu, cikis_no, miktar, belge_tarihi, malzeme_belgesi, islem_turu, malzeme_tanim FROM fason_sap_cikis").fetchall()
    t = {}
    for r in rows:
        a = (r[0] or "", r[1] or "")
        d = t.setdefault(a, {"miktar": 0.0, "tarih": None, "belgeler": set(), "islem": set(), "tanim": r[6] or ""})
        d["miktar"] += r[2] or 0
        if r[3] and (not d["tarih"] or r[3] > d["tarih"]):
            d["tarih"] = r[3]
        d["belgeler"].add(r[4])
        if r[5]:
            d["islem"].add(r[5])
    for d in t.values():
        d["miktar"] = round(d["miktar"], 3)
    return t


def _sap_tol(ref):
    return max(_SAP_TOL_KG, _SAP_TOL_ORAN * abs(ref or 0))


def _sap_durum(bizde, sap, kapsamda, otis_toplam=None):
    """Tek bir (SAP kodu, çıkış no) için durum. SAP çıkışı giriş irsaliyesi bilmediği için SAP'deki toplam,
    o çıkışın OTIS'teki TÜM girişlerdeki toplamıyla (yoksa sistemdeki hareket toplamıyla) karşılaştırılır."""
    sap_m = sap["miktar"] if sap and abs(sap["miktar"]) > _SAP_TOL else None
    if bizde is None and sap_m is None:
        return None
    if bizde is not None and sap_m is None:
        return "sap_yok" if kapsamda else "bilinmiyor"
    if bizde is None:
        return "bizde_yok"
    # SAP; sistemdeki hareket toplamıyla (SAP ölçeği) ya da OTIS toplamlarından biriyle tutuyorsa tamam
    adaylar = [bizde] + (list(otis_toplam) if isinstance(otis_toplam, (tuple, list)) else [otis_toplam])
    if any(c is not None and abs(c - sap_m) <= _sap_tol(c) for c in adaylar):
        return "tamam"
    return "miktar_farki"


def _sap_pay_orani(sap_m, ref):
    """SAP'de yapılan / OTIS toplamı → girişin payının ne kadarı SAP'de düşülmüş (0..1)."""
    if not sap_m or sap_m <= _SAP_TOL:
        return 0.0
    if not ref or ref <= _SAP_TOL or abs(ref - sap_m) <= _sap_tol(ref) or sap_m >= ref:
        return 1.0
    return sap_m / ref


@fason_bp.route("/api/fason/sap-cikis/import", methods=["POST"])
def api_fason_sap_cikis_import():
    """Çıkış ZMM068'ini (eksi miktarlı mal hareketleri) yükler. Aynı belge+kalem tekrar gelirse güncellenir.
    Sistem hareketlerine dokunmaz — sadece karşılaştırma verisi."""
    hata = _oc_yetki()
    if hata:
        return hata
    dosya = request.files.get("dosya")
    if not dosya or not dosya.filename:
        return jsonify({"durum": "hata", "mesaj": "Dosya seçilmedi"}), 400
    try:
        from openpyxl import load_workbook
        ws = load_workbook(dosya, data_only=True, read_only=True).active
        it = ws.iter_rows(values_only=True)
        hdr = [_oc_baslik_norm(h) for h in next(it)]
    except StopIteration:
        return jsonify({"durum": "hata", "mesaj": "Dosya boş"}), 400
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": f"Dosya okunamadı: {e}"}), 400

    def kol(*adlar):
        for a in adlar:
            a = _oc_baslik_norm(a)
            if a in hdr:
                return hdr.index(a)
        return -1

    c_belge, c_yil, c_klm = kol("Malzeme belgesi"), kol("Malzeme belgesi yılı"), kol("Malzeme belgesi klm.")
    c_ref, c_sap, c_tanim = kol("Referans"), kol("Malzeme"), kol("Malzeme kısa metni")
    c_mik, c_tarih, c_islem = kol("Miktar (giriş ÖB)"), kol("Belge tarihi"), kol("İşlem türü")
    c_depo, c_baslik = kol("Kaynak Depo"), kol("Belge başlığı metni")
    eksik = [ad for ad, c in (("Malzeme belgesi", c_belge), ("Referans", c_ref), ("Malzeme", c_sap),
                              ("Miktar (giriş ÖB)", c_mik), ("Belge tarihi", c_tarih)) if c < 0]
    if eksik:
        return jsonify({"durum": "hata", "mesaj": "Beklenen kolonlar bulunamadı: " + ", ".join(eksik) + " — ZMM068 mi?"}), 400

    def al(r, i):
        v = r[i] if 0 <= i < len(r) else None
        return "" if v is None else v

    def metin(v):
        v = str(v).strip()
        return v[:-2] if v.endswith(".0") else v

    kayitlar, ist = [], {"satir": 0, "arti": 0, "atlanan": 0}
    sayac = {}
    for r in it:
        if not r or all(v is None or str(v).strip() == "" for v in r):
            continue
        ist["satir"] += 1
        try:
            m = _tr_sayi(al(r, c_mik))
            tarih = _tarih_iso(al(r, c_tarih))
        except ValueError:
            ist["atlanan"] += 1
            continue
        belge, sap = metin(al(r, c_belge)), metin(al(r, c_sap))
        if not belge or not sap or m is None:
            ist["atlanan"] += 1
            continue
        if m > 0:
            ist["arti"] += 1          # çıkış raporunda artı = ters kayıt (storno) — net toplamda düşülür
        klm = metin(al(r, c_klm))
        if not klm:                    # kalem no yoksa belge içindeki sırasıyla benzersiz yap
            sayac[belge] = sayac.get(belge, 0) + 1
            klm = f"#{sayac[belge]}"
        kayitlar.append((belge, metin(al(r, c_yil)), klm, str(al(r, c_ref)).strip(), sap, str(al(r, c_tanim)).strip(),
                         round(-m, 3), tarih, str(al(r, c_islem)).strip(), str(al(r, c_depo)).strip(), str(al(r, c_baslik)).strip()))
    if not kayitlar:
        return jsonify({"durum": "hata", "mesaj": "Dosyada işlenecek satır yok"}), 400
    if ist["arti"] == len(kayitlar):
        return jsonify({"durum": "hata", "mesaj": "Dosyadaki miktarların hepsi artı — bu bir giriş ZMM068'i gibi. Çıkış ZMM068'ini yükle."}), 400

    conn = get_db()
    try:
        once = conn.execute("SELECT COUNT(*) FROM fason_sap_cikis").fetchone()[0]
        conn.executemany("""
            INSERT INTO fason_sap_cikis (malzeme_belgesi, yil, belge_kalem, cikis_no, sap_kodu, malzeme_tanim, miktar,
                                         belge_tarihi, islem_turu, depo, baslik)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(malzeme_belgesi, yil, belge_kalem) DO UPDATE SET
                cikis_no = excluded.cikis_no, sap_kodu = excluded.sap_kodu, malzeme_tanim = excluded.malzeme_tanim,
                miktar = excluded.miktar, belge_tarihi = excluded.belge_tarihi, islem_turu = excluded.islem_turu,
                depo = excluded.depo, baslik = excluded.baslik, yukleme = datetime('now','localtime')
        """, kayitlar)
        tarihler = [k[7] for k in kayitlar if k[7]]
        # Raporun çekildiği tarih aralığı verildiyse o, yoksa dosyadaki ilk–son belge tarihi
        try:
            f_bas, f_bit = _tarih_iso(request.form.get("bas")), _tarih_iso(request.form.get("bit"))
        except ValueError:
            f_bas = f_bit = None
        k_bas = f_bas or (min(tarihler) if tarihler else None)
        k_bit = f_bit or (max(tarihler) if tarihler else None)
        if k_bas and k_bit and k_bas <= k_bit:
            conn.execute("INSERT INTO fason_sap_yukleme (dosya, bas, bit, satir, yukleyen) VALUES (?, ?, ?, ?, ?)",
                         (dosya.filename, k_bas, k_bit, len(kayitlar), session.get("kullanici", "")))
        conn.commit()
        sonra = conn.execute("SELECT COUNT(*) FROM fason_sap_cikis").fetchone()[0]
        yeni = sonra - once
        log_kaydet("SAP Çıkış Import", f"{len(kayitlar)} satır ({yeni} yeni, {len(kayitlar) - yeni} güncellendi)", None, dosya.filename)
        mesaj = f"{len(kayitlar)} SAP çıkış satırı yüklendi ({yeni} yeni, {len(kayitlar) - yeni} zaten vardı / güncellendi)"
        if ist["arti"]:
            mesaj += f" · {ist['arti']} ters kayıt (storno) satırı net toplamdan düşülecek"
        if ist["atlanan"]:
            mesaj += f" · {ist['atlanan']} satır okunamadı, atlandı"
        return jsonify({"durum": "ok", "mesaj": mesaj, "yuklenen": len(kayitlar), "yeni": yeni, "kapsam": _sap_kapsam(conn)})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


_SAP_LISTELER = ("sap_yok", "bizde_yok", "miktar_farki", "tamam", "fason_disi")


def _sap_karsilastirma_hesapla(conn, listeler, firma="", ara="", bas="", bit="", limit=1000):
    """SAP çıkış ↔ sistem karşılaştırması. listeler: döndürülecek durumlar (sap_yok / bizde_yok / miktar_farki /
    tamam / fason_disi). Sayılar tüm durumlar için, satırlar sadece istenen listeler için (en fazla limit)."""
    kapsam = _sap_kapsam(conn)
    if not kapsam["satir"]:
        return {"kapsam": kapsam, "sayilar": {k: 0 for k in _SAP_LISTELER}, "kg": {}, "satirlar": []}
    sap = _sap_toplamlari(conn)
    sap_nolari = {no for (_, no) in sap.keys()}
    otis_top = _otis_cikis_toplamlari(conn, sap_nolari)
    
    # Sistem tarafı: (SAP kodu, çıkış no) → hareketler (dönüşler hariç)
    bizde = {}
    for r in conn.execute("""
        SELECT c.id, c.kalem_id, c.irsaliye_no, c.miktar, c.tarih, COALESCE(c.tip, 'cikis') AS tip, c.kaynak,
               k.sap_kodu, k.malzeme_tanim, k.giris_miktari, k.durum, NULLIF(k.belge_tarihi, '') AS giris_tarihi,
               i.id AS irsaliye_id, i.irsaliye_no AS giris_no, i.firma_id, COALESCE(f.ad, 'Firma Yok') AS firma
        FROM fason_kalem_cikis c
        JOIN fason_kalem k ON k.id = c.kalem_id
        JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        LEFT JOIN fason_firma f ON f.id = i.firma_id
        WHERE c.irsaliye_no <> '' AND COALESCE(c.tip, 'cikis') IN ('cikis', 'a63') AND COALESCE(k.sap_kodu, '') <> ''   -- nakil / tadilat SAP'de yapılmıyor
    """).fetchall():
        a = (r["sap_kodu"], r["irsaliye_no"])
        d = bizde.setdefault(a, {"miktar": 0.0, "miktar_eksik": False, "tarih": None, "hareketler": [], "kalem": None})
        if r["miktar"] is None:
            d["miktar_eksik"] = True
        d["miktar"] += r["miktar"] or 0
        if r["tarih"] and (not d["tarih"] or r["tarih"] > d["tarih"]):
            d["tarih"] = r["tarih"]
        d["hareketler"].append({"id": r["id"], "miktar": r["miktar"], "tarih": r["tarih"], "tip": r["tip"], "kaynak": r["kaynak"]})
        d["kalem"] = {"id": r["kalem_id"], "malzeme_tanim": r["malzeme_tanim"], "giris": r["giris_miktari"], "durum": r["durum"] or "",
                      "giris_tarihi": r["giris_tarihi"], "irsaliye_id": r["irsaliye_id"], "giris_no": r["giris_no"],
                      "firma_id": r["firma_id"], "firma": r["firma"]}
    
    # SAP'de olup sistemde hareketi olmayanlar için kalem bilgisi (SAP koduyla)
    kalem_sap = {}
    for r in conn.execute("""
        SELECT k.id, k.sap_kodu, k.malzeme_tanim, k.giris_miktari, k.durum, NULLIF(k.belge_tarihi, '') AS giris_tarihi,
               i.id AS irsaliye_id, i.irsaliye_no AS giris_no, i.firma_id, COALESCE(f.ad, 'Firma Yok') AS firma
        FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id LEFT JOIN fason_firma f ON f.id = i.firma_id
        WHERE COALESCE(k.sap_kodu, '') <> ''
    """).fetchall():
        kalem_sap.setdefault(r["sap_kodu"], []).append({
            "id": r["id"], "malzeme_tanim": r["malzeme_tanim"], "giris": r["giris_miktari"], "durum": r["durum"] or "",
            "giris_tarihi": r["giris_tarihi"], "irsaliye_id": r["irsaliye_id"], "giris_no": r["giris_no"],
            "firma_id": r["firma_id"], "firma": r["firma"]})
    
    sayilar = {k: 0 for k in _SAP_LISTELER}
    kg = {k: 0.0 for k in _SAP_LISTELER}
    sonuc = []
    for a in set(bizde) | set(sap):
        sap_kodu, no = a
        b, s_ = bizde.get(a), sap.get(a)
        b_m = None if b is None else round(b["miktar"], 3)
        tarih = (b and b["tarih"]) or (s_ and s_["tarih"])
        # Sistem hareketi SAP verisinin kapsamında mı? (tarih aralığı içinde ya da bu irsaliye SAP'de hiç geçiyorsa)
        kapsamda = _sap_kapsamda(kapsam, tarih) or no in sap_nolari
        durum = _sap_durum(b_m, s_, kapsamda, otis_top.get(a))
        if durum is None or durum == "bilinmiyor":
            continue
        kalem = b["kalem"] if b else None
        if durum == "bizde_yok":
            adaylar = kalem_sap.get(sap_kodu) or []
            if not adaylar:
                durum = "fason_disi"
            else:
                kalem = adaylar[0] if len(adaylar) == 1 else None
        # filtreler
        if bas and (not tarih or tarih[:10] < bas):
            continue
        if bit and (not tarih or tarih[:10] > bit):
            continue
        if firma:
            fid = kalem["firma_id"] if kalem else None
            if firma == "__yok__":
                if fid is not None:
                    continue
            elif str(fid) != firma:
                continue
        tanim = (kalem and kalem["malzeme_tanim"]) or (s_ and s_["tanim"]) or ""
        if ara and ara not in " ".join([sap_kodu, no, tanim, (kalem and kalem["giris_no"]) or "", (kalem and kalem["firma"]) or ""]).lower():
            continue
        sayilar[durum] += 1
        kg[durum] += abs((s_["miktar"] if s_ else 0) or (b_m or 0))
        if durum not in listeler or len(sonuc) >= limit:
            continue
        sonuc.append({
            "sap_kodu": sap_kodu, "cikis_no": no, "tarih": tarih, "malzeme_tanim": tanim, "kalem": kalem,
            "kalem_aday_sayisi": len(kalem_sap.get(sap_kodu) or []),
            "bizde_miktar": b_m, "bizde_miktar_eksik": bool(b and b["miktar_eksik"]),
            "bizde_hareketler": b["hareketler"] if b else [],
            "sap_miktar": s_["miktar"] if s_ else None, "sap_tarih": s_["tarih"] if s_ else None,
            "sap_belgeler": sorted(s_["belgeler"]) if s_ else [], "sap_islem": sorted(s_["islem"]) if s_ else [],
            "otis_toplam": _otis_ref(otis_top.get(a), s_["miktar"] if s_ else None),
            "durum": durum,
            # Fark: OTIS'te bu çıkışın TÜM girişlerdeki toplamı (kantar/SAP ölçeğinden SAP'ye yakın olanı; yoksa sistemdeki) − SAP
            "fark": round((_otis_ref(otis_top.get(a), s_["miktar"] if s_ else None)
                           if otis_top.get(a) is not None else (b_m or 0)) - (s_["miktar"] if s_ else 0), 3),
        })
    sonuc.sort(key=lambda x: (x["tarih"] or "", x["cikis_no"], x["sap_kodu"]))
    return {"kapsam": kapsam, "sayilar": sayilar, "kg": {k: round(v, 2) for k, v in kg.items()}, "satirlar": sonuc}


@fason_bp.route("/api/fason/sap-karsilastirma", methods=["GET"])
def api_fason_sap_karsilastirma():
    """liste: sap_yok / bizde_yok / miktar_farki / tamam / fason_disi. Filtre: firma, ara, bas, bit (çıkış tarihi)."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    liste = request.args.get("liste", "sap_yok")
    if liste not in _SAP_LISTELER:
        liste = "sap_yok"
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 1000))))
    except ValueError:
        limit = 1000
    conn = get_db()
    try:
        r = _sap_karsilastirma_hesapla(conn, {liste}, (request.args.get("firma") or "").strip(),
                                       (request.args.get("ara") or "").strip().lower(),
                                       (request.args.get("bas") or "").strip(), (request.args.get("bit") or "").strip(), limit)
        return jsonify({"durum": "ok", "kapsam": r["kapsam"], "liste": liste, "sayilar": r["sayilar"], "kg": r["kg"],
                        "toplam": r["sayilar"].get(liste, 0), "satirlar": r["satirlar"], "tipler": HAREKET_TIPLERI})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


def _sap_cikis_ayarlari(conn):
    return [dict(r) for r in conn.execute("SELECT yer, siparis, musteri, alici, firma FROM fason_sap_cikis_ayar ORDER BY yer").fetchall()]


def _sap_cikis_ayar_bul(ayarlar, yer):
    """Gönderim yerine uyan ayar (büyük/küçük harf ve Türkçe karakter duyarsız; 'TSM' ↔ 'TSM ENERJİ' gibi kısmi eşleşir)."""
    y = _oc_baslik_norm(yer)
    if not y:
        return None
    for a in ayarlar:
        if _oc_baslik_norm(a["yer"]) == y:
            return a
    for a in ayarlar:
        n = _oc_baslik_norm(a["yer"])
        if n and (y.startswith(n) or n.startswith(y)):
            return a
    return None


@fason_bp.route("/api/fason/sap-cikis/ayarlar", methods=["GET", "POST"])
def api_fason_sap_cikis_ayarlar():
    """SAP çıkış Excel'i sabitleri: gönderim yeri → sipariş / müşteri carisi / alıcı / çıkış firma.
    POST {ayarlar: [{yer, siparis, musteri, alici, firma}]} listeyi bütünüyle değiştirir."""
    hata = _oc_yetki()
    if hata:
        return hata
    conn = get_db()
    try:
        if request.method == "POST":
            liste = (request.get_json() or {}).get("ayarlar") or []
            temiz = []
            for a in liste:
                yer = str(a.get("yer") or "").strip()
                if yer:
                    temiz.append((yer, str(a.get("siparis") or "").strip(), str(a.get("musteri") or "").strip(),
                                  str(a.get("alici") or "").strip(), str(a.get("firma") or "").strip()))
            conn.execute("DELETE FROM fason_sap_cikis_ayar")
            conn.executemany("INSERT OR REPLACE INTO fason_sap_cikis_ayar (yer, siparis, musteri, alici, firma) VALUES (?, ?, ?, ?, ?)", temiz)
            conn.commit()
            log_kaydet("SAP Çıkış Ayarları", f"{len(temiz)} gönderim yeri")
        return jsonify({"durum": "ok", "ayarlar": _sap_cikis_ayarlari(conn)})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


_SAP_EXCEL_SABIT = {"depo": "SA03", "uretim_yeri": 2020, "parti": "DUMMY", "masraf_yeri": "DUMMY2020"}


def _irsaliye_sap_durumu(conn, irs_id):
    """Bir giriş irsaliyesinin kalemlerindeki Satış / A63 çıkışlarının SAP'de yapılıp yapılmadığı.
    Her (kalem, çıkış no) için: sistemdeki miktar, OTIS'te bu girişin payı (kg, alıcı, çıkış sipariş no, gönderim yeri),
    SAP'de bu SAP kodu + çıkış no ile yapılan toplam ve OTIS'teki tüm girişlerin toplamıyla karşılaştırma."""
    irs = conn.execute("SELECT id, irsaliye_no FROM fason_irsaliye WHERE id = ?", (irs_id,)).fetchone()
    if not irs:
        return None
    ks = [dict(r) for r in conn.execute("""
        SELECT id, malzeme_tanim, sap_kodu, otis_sap_kodu, otis_malzeme_tanim, giris_miktari, otis_giris, durum
        FROM fason_kalem WHERE irsaliye_id = ?""", (irs_id,)).fetchall()]
    if not ks:
        return {"irsaliye_no": irs["irsaliye_no"], "kapsam": _sap_kapsam(conn), "satirlar": [], "sayilar": {}}
    kmap = {k["id"]: k for k in ks}
    hareketler = conn.execute(f"""
        SELECT kalem_id, irsaliye_no, COALESCE(tip, 'cikis') AS tip, SUM(COALESCE(miktar, 0)) AS miktar, MAX(tarih) AS tarih,
               MAX(CASE WHEN miktar IS NULL THEN 1 ELSE 0 END) AS eksik
        FROM fason_kalem_cikis
        WHERE kalem_id IN ({','.join('?' * len(ks))}) AND irsaliye_no <> '' AND COALESCE(tip, 'cikis') IN ('cikis', 'a63')
        GROUP BY kalem_id, irsaliye_no, COALESCE(tip, 'cikis')
    """, list(kmap)).fetchall()
    nolar = {h["irsaliye_no"] for h in hareketler}
    kapsam = _sap_kapsam(conn)
    sap = _sap_toplamlari(conn, nolar) if kapsam["satir"] else {}
    sap_nolari = {no for (_, no) in sap.keys()}
    otis_top = _otis_cikis_toplamlari(conn, nolar) if nolar else {}
    # Aynı SAP kodu + çıkış no'lu tüm girişlerimizdeki kesin hareket toplamı (SAP çıkışı giriş bilmez)
    sistem_top = {}
    if nolar:
        for r in conn.execute(f"""
            SELECT k.sap_kodu, c.irsaliye_no, SUM(COALESCE(c.miktar, 0)) FROM fason_kalem_cikis c JOIN fason_kalem k ON k.id = c.kalem_id
            WHERE c.irsaliye_no IN ({','.join('?' * len(nolar))}) AND COALESCE(c.tip, 'cikis') IN ('cikis', 'a63') AND COALESCE(k.sap_kodu, '') <> ''
            GROUP BY k.sap_kodu, c.irsaliye_no""", list(nolar)).fetchall():
            sistem_top[(r[0], r[1])] = r[2] or 0.0

    # OTIS'te bu girişin payı: ham satırlar (giriş = bu irsaliye) → kalem
    pay = {}
    # OTIS'te giriş no birebir ya da (SAP'de IRS ile açıldıysa) firma önekli karşılığı: IRS2026000000030 ↔ MMK2026000000030
    irs_no = irs["irsaliye_no"]
    kosul, prm = "giris_no = ?", [irs_no]
    if irs_no.startswith("IRS") and len(irs_no) == 16:
        kosul += " OR (substr(giris_no, 4) = ? AND length(giris_no) = 16)"
        prm.append(irs_no[3:])
    for r in conn.execute(f"""SELECT cikis_no, sap_kodu, tanim, kg, yer, COALESCE(siparis, '') AS siparis, COALESCE(alici, '') AS alici
                             FROM fason_otis_cikis_satir WHERE {kosul}""", prm).fetchall():
        eslesen, _ = _otis_kalem_eslestir(ks, (r["sap_kodu"] or "").strip(), (r["tanim"] or "").strip())
        if len(eslesen) != 1:
            continue
        a = (eslesen[0]["id"], r["cikis_no"])
        d = pay.setdefault(a, {"kg": 0.0, "yer": "", "siparis": "", "alici": ""})
        d["kg"] += r["kg"] or 0.0
        for alan in ("siparis", "alici"):
            if not d[alan] and r[alan]:
                d[alan] = r[alan]
        if not d["yer"] and r["yer"]:
            d["yer"] = (r["yer"] or "").partition(" · ")[0].strip()

    satirlar, sayilar = [], {}
    for h in hareketler:
        k = kmap[h["kalem_id"]]
        no, sap_kodu = h["irsaliye_no"], (k["sap_kodu"] or "")
        s_ = sap.get((sap_kodu, no)) if sap_kodu else None
        otis_ = otis_top.get((sap_kodu, no)) if sap_kodu else None
        if not sap_kodu:
            durum = "sap_kodsuz"
        elif not kapsam["satir"]:
            durum = "bilinmiyor"
        else:
            kapsamda = _sap_kapsamda(kapsam, h["tarih"]) or no in sap_nolari
            st_ = round(sistem_top.get((sap_kodu, no), h["miktar"]), 3)
            durum = _sap_durum(st_, s_, kapsamda, otis_)
            if durum == "miktar_farki":
                durum = "sap_eksik" if s_["miktar"] < st_ else "sap_fazla"
        p_ = pay.get((k["id"], no)) or {}
        # SAP'ye yazılacak miktar: OTIS sadece teyit; giriş OTIS'le uyuşuyorsa kalemin SAP giriş kg'si.
        # Kalemin tamamı bu çıkışla gittiyse SAP giriş kg'nin tamamı, kısmi çıkışta OTIS oranında payı.
        sap_giris = k["giris_miktari"] or 0.0
        sap_yazilacak = None
        if p_.get("kg") and sap_giris > 0:
            og = k["otis_giris"] or 0.0
            if og <= 0 or abs(p_["kg"] - og) <= max(0.5, og * 0.02) or p_["kg"] > og:
                sap_yazilacak = round(sap_giris, 3)
            else:
                sap_yazilacak = round(sap_giris * p_["kg"] / og, 3)
        sayilar[durum] = sayilar.get(durum, 0) + 1
        satirlar.append({
            "kalem_id": k["id"], "malzeme_tanim": k["malzeme_tanim"], "sap_kodu": sap_kodu, "durum_kalem": k["durum"] or "",
            "cikis_no": no, "tip": h["tip"], "tarih": h["tarih"], "bizde": round(h["miktar"], 3), "miktar_eksik": bool(h["eksik"]),
            "otis_kg": round(p_["kg"], 3) if p_.get("kg") else None, "alici": p_.get("alici") or "",
            "sap_giris": round(sap_giris, 3) if sap_giris else None, "sap_yazilacak": sap_yazilacak,
            "siparis": p_.get("siparis") or "", "yer": p_.get("yer") or "",
            "sap": s_["miktar"] if s_ else None, "sap_belgeler": sorted(s_["belgeler"]) if s_ else [],
            "otis_toplam": _otis_ref(otis_, s_["miktar"] if s_ else None), "durum": durum,
        })
    sira = {"sap_yok": 0, "sap_eksik": 1, "sap_fazla": 2, "bilinmiyor": 3, "sap_kodsuz": 4, "tamam": 5}
    satirlar.sort(key=lambda x: (sira.get(x["durum"], 9), x["tarih"] or "", x["cikis_no"], x["malzeme_tanim"]))
    return {"irsaliye_no": irs["irsaliye_no"], "kapsam": kapsam, "satirlar": satirlar, "sayilar": sayilar}


@fason_bp.route("/api/fason/irsaliye/<int:irs_id>/sap-durum", methods=["GET"])
def api_fason_irsaliye_sap_durum(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        d = _irsaliye_sap_durumu(conn, irs_id)
        if d is None:
            return jsonify({"durum": "hata", "mesaj": "İrsaliye bulunamadı"}), 404
        return jsonify(dict(d, durum="ok"))
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


def _excel_tarih(t):
    """ISO tarih → Excel tarihi (datetime); çözülemezse metin olarak bırakır."""
    if not t:
        return None
    try:
        return datetime.strptime(str(t)[:10], "%Y-%m-%d")
    except ValueError:
        return t


@fason_bp.route("/api/fason/irsaliye/<int:irs_id>/sap-cikis-excel", methods=["GET"])
def api_fason_irsaliye_sap_cikis_excel(irs_id):
    """Bu giriş irsaliyesinde SAP'de HİÇ yapılmamış satış çıkışlarını SAP çıkış formatında Excel'e döker.
    41'li ve 47'li kodlar ayrı dosyaya (ikisi de varsa iki dosya). Sabit: Depo Yr., Sipariş, Müşteri, Üretim Yeri,
    Parti, Masraf Yeri. OTIS sadece teyit + Alıcı (TESLİM ALAN), Metin (ÇIKIŞ SİPARİŞ NO), Çıkış İrsaliyesi;
    SAP Kodu ve Miktar bizim sistemdeki eşleşmiş kalemden (SAP kodu, SAP giriş kg — kısmi çıkışta OTIS oranında pay).
    Çıkış Firma gönderim yerine göre tek tip. OTIS'te bu giriş irsaliyesiyle teyit edilmeyen satırlar yazılmaz."""
    hata = _oc_yetki()
    if hata:
        return hata
    conn = get_db()
    try:
        d = _irsaliye_sap_durumu(conn, irs_id)
        if d is None:
            return jsonify({"durum": "hata", "mesaj": "İrsaliye bulunamadı"}), 404
        ayarlar = _sap_cikis_ayarlari(conn)
    finally:
        conn.close()
    gruplar, disarida = {"41": [], "47": []}, []
    for x in d["satirlar"]:
        if x["durum"] != "sap_yok" or x["tip"] != "cikis":
            continue
        miktar = x["sap_yazilacak"]
        ayar = _sap_cikis_ayar_bul(ayarlar, x["yer"])
        grup = x["sap_kodu"][:2]
        neden = ("SAP kodu 41 / 47 ile başlamıyor" if grup not in gruplar else
                 "OTIS'te bu çıkış, bu giriş irsaliyesiyle bulunamadı (teyit yok)" if not x["otis_kg"] else
                 f"Gönderim yeri için sipariş / müşteri ayarı yok ({x['yer']})" if not ayar else
                 "Kalemin SAP giriş kg'si yok" if not miktar else "")
        if neden:
            disarida.append(f"{x['cikis_no']} · {x['malzeme_tanim']}: {neden}")
            continue
        gruplar[grup].append(dict(x, miktar_excel=round(miktar, 3), ayar=ayar))
    if not gruplar["41"] and not gruplar["47"]:
        return jsonify({"durum": "hata", "mesaj": "Bu irsaliyede Excel'e yazılacak, SAP'de hiç yapılmamış satış çıkışı yok"
                        + (" · " + "; ".join(disarida[:5]) if disarida else "")}), 400
    try:
        from openpyxl import Workbook
        klasor = os.path.join(_fason_export_klasor(), "exports", "sap_cikis")
        os.makedirs(klasor, exist_ok=True)
        sayi_mi = lambda v: int(v) if str(v).isdigit() else v
        dosyalar, bos_metin = [], 0
        for grup, liste in gruplar.items():
            if not liste:
                continue
            wb = Workbook()
            ws = wb.active
            ws.title = "Sayfa1"
            ws.append(["SAP Kodu", "Miktar", "Depo Yr.", "Sipariş", "Müşteri", "Üretim Yeri", "Alıcı", "Parti", "Metin",
                       "Masraf Yeri", "Çıkış İrsaliyesi", "Çıkış Firma" if grup == "41" else "Çıkış Firması", "Çıkış Tarihi"])
            for x in sorted(liste, key=lambda x: (x["cikis_no"], x["sap_kodu"])):
                a = x["ayar"]
                bos_metin += 0 if x["siparis"] else 1
                ws.append([sayi_mi(x["sap_kodu"]), x["miktar_excel"], _SAP_EXCEL_SABIT["depo"], a["siparis"], sayi_mi(a["musteri"]),
                           _SAP_EXCEL_SABIT["uretim_yeri"], x["alici"], _SAP_EXCEL_SABIT["parti"], x["siparis"],
                           _SAP_EXCEL_SABIT["masraf_yeri"], x["cikis_no"], a["firma"] or x["yer"], _excel_tarih(x["tarih"])])
                ws.cell(row=ws.max_row, column=13).number_format = "DD.MM.YYYY"
            for harf, gen in zip("ABCDEFGHIJKLM", (13.3, 10, 11, 13.3, 11, 11, 16.4, 8, 32.7, 12, 18, 18, 12)):
                ws.column_dimensions[harf].width = gen
            dosya_adi = f"sap_cikis_{d['irsaliye_no']}_{grup}li_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
            wb.save(os.path.join(klasor, dosya_adi))
            dosyalar.append(f"{dosya_adi} ({len(liste)} satır)")
        try:
            os.startfile(klasor)
        except Exception:
            pass
        log_kaydet("SAP Çıkış Excel'i", f"{d['irsaliye_no']}: " + ", ".join(dosyalar), irs_id, d["irsaliye_no"])
        mesaj = "Hazırlandı: " + ", ".join(dosyalar)
        if bos_metin:
            mesaj += f" · {bos_metin} satırda OTIS alıcı / çıkış sipariş no bulunamadı (OTIS çıkış export'unu tekrar yükle)"
        if disarida:
            mesaj += f" · Dışarıda kalan {len(disarida)}: " + "; ".join(disarida[:3]) + ("…" if len(disarida) > 3 else "")
        return jsonify({"durum": "ok", "mesaj": mesaj, "dosyalar": dosyalar})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@fason_bp.route("/api/fason/sap-cikis/hareket-ekle", methods=["POST"])
def api_fason_sap_cikis_hareket_ekle():
    """'Sistemde yok' listesinden seçilen SAP çıkışlarını kaleme hareket olarak yazar (kaynak 'sap').
    Body: {satirlar: [{sap_kodu, cikis_no, kalem_id?}], tip}"""
    hata = _oc_yetki()
    if hata:
        return hata
    d = request.get_json() or {}
    tip = d.get("tip") or "cikis"
    if tip not in HAREKET_TIPLERI or tip == "tadilat_donus":
        return jsonify({"durum": "hata", "mesaj": "Hareket tipi seç"}), 400
    satirlar = d.get("satirlar") or []
    if not satirlar:
        return jsonify({"durum": "hata", "mesaj": "Satır seçilmedi"}), 400
    kullanici = session.get("kullanici", "")
    conn = get_db()
    try:
        sap = _sap_toplamlari(conn, {str(s.get("cikis_no") or "") for s in satirlar})
        eklenen = guncellenen = atlanan = durum_degisen = 0
        for s in satirlar:
            a = (str(s.get("sap_kodu") or ""), str(s.get("cikis_no") or ""))
            veri = sap.get(a)
            if not veri or veri["miktar"] <= _SAP_TOL or not a[1]:
                atlanan += 1
                continue
            kid = s.get("kalem_id")
            if not kid:
                ks = conn.execute("SELECT id FROM fason_kalem WHERE sap_kodu = ?", (a[0],)).fetchall()
                if len(ks) != 1:
                    atlanan += 1
                    continue
                kid = ks[0][0]
            var = conn.execute("SELECT id FROM fason_kalem_cikis WHERE kalem_id = ? AND irsaliye_no = ?", (kid, a[1])).fetchone()
            if var:
                conn.execute("UPDATE fason_kalem_cikis SET miktar = ?, tarih = ?, tip = ?, kaynak = 'sap' WHERE id = ?",
                             (veri["miktar"], veri["tarih"], tip, var[0]))
                guncellenen += 1
            else:
                conn.execute("""INSERT INTO fason_kalem_cikis (kalem_id, irsaliye_no, miktar, tarih, kaynak, ekleyen, tip)
                                VALUES (?, ?, ?, ?, 'sap', ?, ?)""", (kid, a[1], veri["miktar"], veri["tarih"], kullanici, tip))
                eklenen += 1
            _cikis_ozet_guncelle(conn, kid)
            if _hareket_sonrasi_durum(conn, kid, tip):
                durum_degisen += 1
        conn.commit()
        log_kaydet("SAP Çıkıştan Hareket", f"{eklenen} eklendi, {guncellenen} güncellendi, {atlanan} atlandı ({HAREKET_TIPLERI[tip]})")
        mesaj = f"{eklenen} hareket eklendi" + (f", {guncellenen} güncellendi" if guncellenen else "")
        if durum_degisen:
            mesaj += f" · {durum_degisen} kalemin durumu güncellendi"
        if atlanan:
            mesaj += f" · {atlanan} satır atlandı (kalem tek değil ya da SAP net miktarı yok)"
        return jsonify({"durum": "ok", "mesaj": mesaj, "eklenen": eklenen, "guncellenen": guncellenen, "atlanan": atlanan})
    except Exception as e:
        conn.rollback()
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/sap-cikis/temizle", methods=["POST"])
def api_fason_sap_cikis_temizle():
    """Yüklenmiş SAP çıkış verisini siler (yanlış dosya yüklendiyse). Sistem hareketlerine dokunmaz."""
    hata = _oc_yetki()
    if hata:
        return hata
    conn = get_db()
    try:
        n = conn.execute("DELETE FROM fason_sap_cikis").rowcount
        conn.execute("DELETE FROM fason_sap_yukleme")
        conn.commit()
        log_kaydet("SAP Çıkış Verisi Silindi", f"{n} satır")
        return jsonify({"durum": "ok", "mesaj": f"{n} SAP çıkış satırı silindi"})
    finally:
        conn.close()


@fason_bp.route("/api/fason/db-senkronize-et", methods=["POST"])
def api_fason_db_senkronize_et():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("rol") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Sadece admin"}), 403
    try:
        if not os.path.isdir(ORTAK_KLASOR):
            return jsonify({"durum": "hata", "mesaj": "K: sürücüsüne şu an erişilemiyor"}), 500

        hedef_yol = os.path.join(ORTAK_KLASOR, "fason.db")
        kaynak = sqlite3.connect(DB_YOL)
        hedef = sqlite3.connect(hedef_yol)
        kaynak.backup(hedef)
        # Gönderilen içeriğin özeti (K:'deki kopyadan okunur — gerçekten yazıldığını da doğrular)
        irsaliye_sayisi = hedef.execute("SELECT COUNT(*) FROM fason_irsaliye").fetchone()[0]
        kalem_sayisi = hedef.execute("SELECT COUNT(*) FROM fason_kalem").fetchone()[0]
        hedef.close()
        kaynak.close()

        try:
            boyut_mb = round(os.path.getsize(hedef_yol) / (1024 * 1024), 1)
        except OSError:
            boyut_mb = None
        zaman = datetime.now().strftime("%d.%m.%Y %H:%M")
        log_kaydet("Fason DB K:'ya Gönderildi",
                   f"{irsaliye_sayisi} irsaliye, {kalem_sayisi} kalem" + (f", {boyut_mb} MB" if boyut_mb is not None else ""),
                   None, "fason.db")

        return jsonify({
            "durum": "ok",
            "mesaj": "fason.db K: sürücüsüne gönderildi (üzerine yazıldı)",
            "irsaliye_sayisi": irsaliye_sayisi,
            "kalem_sayisi": kalem_sayisi,
            "boyut_mb": boyut_mb,
            "zaman": zaman
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


# ══════════════════════════ RAPOR (canlı DB'den, salt okunur) ══════════════════════════

_RAPOR_CIKIS_DURUMLARI = ("Çıkış", "Çıkış (A63)")


@fason_bp.route("/api/fason/rapor/ozet", methods=["GET"])
def api_fason_rapor_ozet():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    where, params = _rapor_filtre()
    conn = get_db()
    try:
        if where == "1=1":
            toplam_irsaliye = conn.execute("SELECT COUNT(*) FROM fason_irsaliye").fetchone()[0]
        else:
            toplam_irsaliye = conn.execute(f"SELECT COUNT(DISTINCT i.id) FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE {where}", params).fetchone()[0]
        toplam_kalem = conn.execute(f"SELECT COUNT(*) FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id WHERE {where}", params).fetchone()[0]

        toplamlar = conn.execute(f"""
            SELECT
                COALESCE(SUM(k.giris_miktari), 0) AS toplam_giris,
                COALESCE(SUM(k.otis_giris), 0)    AS toplam_otis_giris,
                COALESCE(SUM(k.toplam_fiyat), 0)  AS toplam_tutar
            FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE {where}
        """, params).fetchone()

        kantar_farkli_satir = conn.execute(f"""
            SELECT COUNT(*) AS adet,
                   COALESCE(SUM(ABS(COALESCE(k.otis_giris, 0) - COALESCE(k.giris_miktari, 0))), 0) AS toplam_kg,
                   COALESCE(SUM(CASE WHEN COALESCE(k.giris_miktari, 0) > COALESCE(k.otis_giris, 0)
                                      THEN k.giris_miktari - k.otis_giris ELSE 0 END), 0) AS fazla_kg,
                   COALESCE(SUM(CASE WHEN COALESCE(k.otis_giris, 0) > COALESCE(k.giris_miktari, 0)
                                      THEN k.otis_giris - k.giris_miktari ELSE 0 END), 0) AS eksik_kg
            FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE {where} AND ABS(COALESCE(k.otis_giris, 0) - COALESCE(k.giris_miktari, 0)) > 0.01
        """, params).fetchone()

        stokta_bekleyen = conn.execute(f"""
            SELECT COALESCE(SUM(k.giris_miktari), 0) AS toplam_kg,
                   COALESCE(SUM(k.toplam_fiyat), 0)   AS toplam_tutar
            FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE {where} AND (k.durum IS NULL OR k.durum = '' OR k.durum = 'Stok')
        """, params).fetchone()

        return jsonify({
            "toplam_irsaliye": toplam_irsaliye,
            "toplam_kalem": toplam_kalem,
            "toplam_giris_miktari": toplamlar["toplam_giris"],
            "toplam_otis_giris_miktari": toplamlar["toplam_otis_giris"],
            "toplam_tutar": toplamlar["toplam_tutar"],
            "kantar_farkli_kalem": kantar_farkli_satir["adet"],
            "kantar_farkli_toplam_kg": kantar_farkli_satir["toplam_kg"],
            "kantar_farkli_fazla_kg": kantar_farkli_satir["fazla_kg"],
            "kantar_farkli_eksik_kg": kantar_farkli_satir["eksik_kg"],
            "stokta_bekleyen_kg": stokta_bekleyen["toplam_kg"],
            "stokta_bekleyen_tutar": stokta_bekleyen["toplam_tutar"],
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/durum-dagilimi", methods=["GET"])
def api_fason_rapor_durum_dagilimi():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    where, params = _rapor_filtre()
    conn = get_db()
    try:
        # GROUP BY 1: NULL ve '' ayrı gruplanıp "Belirlenmedi" iki kez çıkmasın
        rows = conn.execute(f"""
            SELECT
                CASE WHEN k.durum IS NULL OR k.durum = '' THEN 'Belirlenmedi' ELSE k.durum END AS durum,
                COUNT(*) AS adet,
                COALESCE(SUM(k.giris_miktari), 0) AS miktar
            FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE {where}
            GROUP BY 1
            ORDER BY adet DESC
        """, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/trend", methods=["GET"])
def api_fason_rapor_trend():
    """Giriş (irsaliye_tarihi) ve çıkış (durum_tarihi, durum Çıkış/Çıkış (A63) olanlar)
    için zaman serisi. Çıkış tarafı yalnızca durum_tarihi kolonu eklendikten SONRA
    durumu değişen kalemleri kapsar — bkz. _migrate_ek_kolonlar üzerindeki not."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401

    baslangic = request.args.get("baslangic", "").strip()
    bitis = request.args.get("bitis", "").strip()
    gruplama = request.args.get("gruplama", "ay").strip()
    uzunluk = {"gun": 10, "hafta": 10, "ay": 7}.get(gruplama, 7)

    conn = get_db()
    try:
        giris_sql = f"""
            SELECT substr(irsaliye_tarihi, 1, {uzunluk}) AS donem,
                   COUNT(*) AS adet, COALESCE(SUM(giris_miktari), 0) AS miktar
            FROM fason_kalem
            WHERE irsaliye_tarihi IS NOT NULL AND irsaliye_tarihi != ''
        """
        params_giris = []
        if baslangic:
            giris_sql += " AND irsaliye_tarihi >= ?"
            params_giris.append(baslangic)
        if bitis:
            giris_sql += " AND irsaliye_tarihi <= ?"
            params_giris.append(bitis)
        giris_sql += " GROUP BY donem ORDER BY donem"
        giris_rows = conn.execute(giris_sql, params_giris).fetchall()

        yer_tutucu = ",".join("?" * len(_RAPOR_CIKIS_DURUMLARI))
        cikis_sql = f"""
            SELECT substr(durum_tarihi, 1, {uzunluk}) AS donem,
                   COUNT(*) AS adet, COALESCE(SUM(giris_miktari), 0) AS miktar
            FROM fason_kalem
            WHERE durum IN ({yer_tutucu}) AND durum_tarihi IS NOT NULL AND durum_tarihi != ''
        """
        params_cikis = list(_RAPOR_CIKIS_DURUMLARI)
        if baslangic:
            cikis_sql += " AND durum_tarihi >= ?"
            params_cikis.append(baslangic)
        if bitis:
            cikis_sql += " AND durum_tarihi <= ?"
            params_cikis.append(bitis)
        cikis_sql += " GROUP BY donem ORDER BY donem"
        cikis_rows = conn.execute(cikis_sql, params_cikis).fetchall()

        return jsonify({
            "gruplama": gruplama,
            "giris": [dict(r) for r in giris_rows],
            "cikis": [dict(r) for r in cikis_rows],
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/firma-analiz", methods=["GET"])
def api_fason_rapor_firma_analiz():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    where, params = _rapor_filtre()
    conn = get_db()
    try:
        rows = conn.execute(f"""
            SELECT COALESCE(f.ad, 'Firma Yok') AS firma,
                   COUNT(DISTINCT i.id) AS irsaliye_sayisi,
                   COALESCE(SUM(k.giris_miktari), 0) AS toplam_giris,
                   COALESCE(SUM(k.toplam_fiyat), 0) AS toplam_tutar
            FROM fason_kalem k
            JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            LEFT JOIN fason_firma f ON f.id = i.firma_id
            WHERE {where}
            GROUP BY firma
            ORDER BY irsaliye_sayisi DESC
        """, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/kantar-farki", methods=["GET"])
def api_fason_rapor_kantar_farki():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    ara = request.args.get("ara", "").strip()
    try:
        limit = max(1, min(1000, int(request.args.get("limit", 200))))
    except ValueError:
        limit = 200

    conn = get_db()
    try:
        sorgu = """
            SELECT k.id, i.id AS irsaliye_id, i.irsaliye_no, COALESCE(f.ad, 'Firma Yok') AS firma_ad,
                   NULLIF(k.belge_tarihi, '') AS giris_tarihi,
                   k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.otis_giris,
                   k.fark_sebebi, k.durum,
                   ABS(COALESCE(k.otis_giris, 0) - COALESCE(k.giris_miktari, 0)) AS fark
            FROM fason_kalem k
            JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            LEFT JOIN fason_firma f ON f.id = i.firma_id
            WHERE ABS(COALESCE(k.otis_giris, 0) - COALESCE(k.giris_miktari, 0)) > 0.01
              AND k.fark_sebebi = 'Kantar Farkı'
        """
        filtre_where, filtre_params = _rapor_filtre()
        sorgu += f" AND {filtre_where}"
        params = list(filtre_params)
        if ara:
            # 'durum' da arama alanına dahil — kullanıcı "stok" ya da "çıkış" yazınca
            # o durumdaki kalemler de eşleşsin diye (bkz. kullanıcı isteği).
            sorgu += " AND (k.malzeme_tanim LIKE ? OR i.irsaliye_no LIKE ? OR f.ad LIKE ? OR k.durum LIKE ? OR k.sap_kodu LIKE ?)"
            params += [f"%{ara}%"] * 5
        sorgu += " ORDER BY fark DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sorgu, params).fetchall()
        toplam = conn.execute(f"""
            SELECT COUNT(*) FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
            WHERE ABS(COALESCE(k.otis_giris, 0) - COALESCE(k.giris_miktari, 0)) > 0.01
              AND k.fark_sebebi = 'Kantar Farkı' AND {filtre_where}
        """, filtre_params).fetchone()[0]

        return jsonify({"toplam": toplam, "gosterilen": len(rows), "kalemler": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


# ══════════════════════════ RAPOR v2 — malzeme konumu, hareket trendi, takip listeleri ══════════════════════════
# Filtreler (tüm rapor uç noktaları): ?baslangic=YYYY-MM-DD&bitis=YYYY-MM-DD&firma=<id|__yok__>
#  - Tarih aralığı kalemin GİRİŞ tarihine uygulanır (ZMM068 belge tarihi; belge tarihi olmayan kalem tarih filtresine girmez).
#  - Aylık hareket trendinde ise hareketin kendi tarihine uygulanır.

# Giriş tarihi = SADECE ZMM068 "Belge tarihi". Elle girilen irsaliye tarihi yanıltıcı olabileceği için kullanılmaz.
_GIRIS_TARIHI_SQL = "NULLIF(k.belge_tarihi, '')"


def _rapor_tarih_arg(ad):
    import re as _re
    v = (request.args.get(ad) or "").strip()
    return v if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""


def _rapor_filtre(tarih_alani=_GIRIS_TARIHI_SQL, tarih_uygula=True):
    """(where_sql, params) — k (fason_kalem) ve i (fason_irsaliye) takma adlarını bekler."""
    kosullar, params = [], []
    firma = (request.args.get("firma") or "").strip()
    if firma == "__yok__":
        kosullar.append("i.firma_id IS NULL")
    elif firma:
        try:
            params.append(int(firma)); kosullar.append("i.firma_id = ?")
        except ValueError:
            pass
    if tarih_uygula:
        b, s = _rapor_tarih_arg("baslangic"), _rapor_tarih_arg("bitis")
        if b:
            kosullar.append(f"{tarih_alani} >= ?"); params.append(b)
        if s:
            kosullar.append(f"{tarih_alani} <= ?"); params.append(s + " 99")   # gün sonu dahil
    return (" AND ".join(kosullar) or "1=1"), params


_HAREKET_TOPLAM_SQL = """
    SELECT kalem_id, COUNT(*) AS hareket_sayisi,
           SUM(CASE WHEN COALESCE(tip,'cikis') IN ('cikis','a63') THEN COALESCE(miktar,0) ELSE 0 END) AS kesin,
           SUM(CASE WHEN tip = 'tadilat_gidis' THEN COALESCE(miktar,0) ELSE 0 END) AS gidis,
           SUM(CASE WHEN tip = 'tadilat_donus' THEN COALESCE(miktar,0) ELSE 0 END) AS donus,
           SUM(CASE WHEN tip = 'nakil' THEN COALESCE(miktar,0) ELSE 0 END) AS nakil,
           MAX(CASE WHEN miktar IS NULL THEN 1 ELSE 0 END) AS miktari_eksik
    FROM fason_kalem_cikis GROUP BY kalem_id
"""
_DURUM_KONUM = {"Çıkış": "kesin", "Çıkış (A63)": "kesin", "Aspro": "kesin", "Tadilat": "tadilatta", "Nakil": "nakilde"}


def _kalem_konumlari(conn):
    """Filtredeki her kalem için konum: depoda / tadilatta / nakilde / kesin (kg).
    Hareket kaydı (miktarlarıyla) varsa ondan hesaplanır; yoksa ya da miktarı eksikse durumdan tahmin edilir."""
    where, params = _rapor_filtre()
    rows = conn.execute(f"""
        SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.giris_miktari, k.durum, k.toplam_fiyat,
               i.id AS irsaliye_id, i.irsaliye_no, i.firma_id, COALESCE(f.ad, 'Firma Yok') AS firma,
               {_GIRIS_TARIHI_SQL} AS giris_tarihi,
               COALESCE(h.hareket_sayisi, 0) AS hareket_sayisi, h.kesin, h.gidis, h.donus, h.nakil,
               COALESCE(h.miktari_eksik, 0) AS miktari_eksik
        FROM fason_kalem k
        JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        LEFT JOIN fason_firma f ON f.id = i.firma_id
        LEFT JOIN ({_HAREKET_TOPLAM_SQL}) h ON h.kalem_id = k.id
        WHERE {where}
    """, params).fetchall()
    sonuc = []
    for r in rows:
        giris = r["giris_miktari"] or 0.0
        d = {"id": r["id"], "malzeme_tanim": r["malzeme_tanim"], "sap_kodu": r["sap_kodu"], "giris": giris,
             "durum": r["durum"] or "", "tutar": r["toplam_fiyat"] or 0.0, "irsaliye_id": r["irsaliye_id"],
             "irsaliye_no": r["irsaliye_no"], "firma_id": r["firma_id"], "firma": r["firma"],
             "giris_tarihi": r["giris_tarihi"], "hareket_sayisi": r["hareket_sayisi"],
             "miktari_eksik": bool(r["miktari_eksik"]), "kalan": None}
        if r["hareket_sayisi"] and not r["miktari_eksik"] and r["giris_miktari"] is not None:
            oz = _hareket_hesapla(giris, r["kesin"], r["gidis"], r["donus"], r["nakil"], False)
            d.update(depoda=oz["depoda"], tadilatta=oz["tadilatta"], nakilde=oz["nakilde"],
                     kesin=oz["kesin_cikan"], kalan=oz["kalan"], kaynak="hareket")
        else:
            konum = _DURUM_KONUM.get(d["durum"], "depoda")
            d.update(depoda=0.0, tadilatta=0.0, nakilde=0.0, kesin=0.0, kaynak="durum")
            d[konum] = giris
        sonuc.append(d)
    return sonuc


def _rapor_konum_ozet(kalemler):
    alanlar = ("giris", "depoda", "tadilatta", "nakilde", "kesin", "tutar")
    toplam = {a: 0.0 for a in alanlar}
    firmalar = {}
    for k in kalemler:
        f = firmalar.setdefault(k["firma"], dict({a: 0.0 for a in alanlar}, firma=k["firma"], firma_id=k["firma_id"],
                                                  kalem=0, irsaliyeler=set(), durumdan=0))
        for a in alanlar:
            toplam[a] += k[a] or 0
            f[a] += k[a] or 0
        f["kalem"] += 1
        f["irsaliyeler"].add(k["irsaliye_id"])
        if k["kaynak"] == "durum" and k["durum"] in _DURUM_KONUM:
            f["durumdan"] += 1
    firma_listesi = []
    for f in firmalar.values():
        f["irsaliye"] = len(f.pop("irsaliyeler"))
        f["kalan"] = f["giris"] - f["kesin"]
        f["cikis_orani"] = round(100 * f["kesin"] / f["giris"], 1) if f["giris"] > 0 else None
        firma_listesi.append({a: (round(v, 3) if isinstance(v, float) else v) for a, v in f.items()})
    firma_listesi.sort(key=lambda x: -x["giris"])
    return {
        "toplam": {a: round(v, 3) for a, v in toplam.items()},
        "kalem_sayisi": len(kalemler),
        "irsaliye_sayisi": len({k["irsaliye_id"] for k in kalemler}),
        "durumdan_tahmini": sum(1 for k in kalemler if k["kaynak"] == "durum" and k["durum"] in _DURUM_KONUM),
        "miktari_eksik": sum(1 for k in kalemler if k["miktari_eksik"]),
        "giris_tarihsiz": sum(1 for k in kalemler if not k["giris_tarihi"]),
        "firmalar": firma_listesi,
    }


def _rapor_hareket_trend(conn):
    """Aylık: giriş (giriş tarihine göre) + hareket tipleri (hareket tarihine göre), kg."""
    where_g, params_g = _rapor_filtre()
    giris = conn.execute(f"""
        SELECT substr({_GIRIS_TARIHI_SQL}, 1, 7) AS ay, COALESCE(SUM(k.giris_miktari), 0) AS kg, COUNT(*) AS adet
        FROM fason_kalem k JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        WHERE {where_g} AND {_GIRIS_TARIHI_SQL} IS NOT NULL
        GROUP BY ay ORDER BY ay
    """, params_g).fetchall()
    where_h, params_h = _rapor_filtre(tarih_alani="c.tarih")
    hareket = conn.execute(f"""
        SELECT substr(c.tarih, 1, 7) AS ay, COALESCE(c.tip, 'cikis') AS tip, COALESCE(SUM(c.miktar), 0) AS kg, COUNT(*) AS adet
        FROM fason_kalem_cikis c
        JOIN fason_kalem k ON k.id = c.kalem_id JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        WHERE {where_h} AND COALESCE(c.tarih, '') <> ''
        GROUP BY ay, tip ORDER BY ay
    """, params_h).fetchall()
    where_t, params_t = _rapor_filtre(tarih_uygula=False)
    tarihsiz = conn.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(c.miktar), 0) FROM fason_kalem_cikis c
        JOIN fason_kalem k ON k.id = c.kalem_id JOIN fason_irsaliye i ON i.id = k.irsaliye_id
        WHERE {where_t} AND COALESCE(c.tarih, '') = ''
    """, params_t).fetchone()
    aylar = sorted({r["ay"] for r in giris if r["ay"]} | {r["ay"] for r in hareket if r["ay"]})
    seri = {ad: {a: 0.0 for a in aylar} for ad in ("giris", "kesin", "tadilat_gidis", "tadilat_donus", "nakil")}
    for r in giris:
        if r["ay"]:
            seri["giris"][r["ay"]] = round(r["kg"], 3)
    for r in hareket:
        ad = "kesin" if r["tip"] in KESIN_CIKIS_TIPLERI else r["tip"]
        if ad in seri and r["ay"]:
            seri[ad][r["ay"]] = round(seri[ad][r["ay"]] + r["kg"], 3)
    return {"aylar": aylar, "seriler": {ad: [v[a] for a in aylar] for ad, v in seri.items()},
            "tarihsiz_hareket": tarihsiz[0], "tarihsiz_kg": round(tarihsiz[1], 3)}


def _rapor_takip(conn, kalemler):
    """Tadilatta / Akkuyu'da bekleyenler (kaç gündür) + kontrol gereken kalemler."""
    bugun = datetime.now().date()
    ilgili = [k for k in kalemler if (k["tadilatta"] or 0) > 0.005 or (k["nakilde"] or 0) > 0.005]
    son_gidis = {}
    if ilgili:
        idler = [k["id"] for k in ilgili]
        for i in range(0, len(idler), 900):
            parca = idler[i:i + 900]
            for r in conn.execute(f"""
                SELECT kalem_id, COALESCE(tip, 'cikis') AS tip, irsaliye_no, tarih FROM fason_kalem_cikis
                WHERE kalem_id IN ({','.join('?' * len(parca))}) AND tip IN ('tadilat_gidis', 'nakil')
                ORDER BY COALESCE(tarih, ''), id
            """, parca).fetchall():
                son_gidis[(r["kalem_id"], r["tip"])] = (r["irsaliye_no"], r["tarih"])

    def bekleyen(k, alan, tip):
        no, tarih = son_gidis.get((k["id"], tip), ("", None))
        gun = None
        if tarih:
            try:
                gun = (bugun - datetime.strptime(tarih[:10], "%Y-%m-%d").date()).days
            except ValueError:
                pass
        return {"id": k["id"], "irsaliye_id": k["irsaliye_id"], "irsaliye_no": k["irsaliye_no"], "firma": k["firma"],
                "giris_tarihi": k["giris_tarihi"],
                "malzeme_tanim": k["malzeme_tanim"], "sap_kodu": k["sap_kodu"], "kg": round(k[alan], 3),
                "gonderim_irsaliye": no, "gonderim_tarihi": tarih, "gun": gun,
                "kaynak": k["kaynak"]}

    sirala = lambda l: sorted(l, key=lambda x: (x["gun"] is None, -(x["gun"] or 0), -x["kg"]))
    tadilatta = sirala([bekleyen(k, "tadilatta", "tadilat_gidis") for k in kalemler if (k["tadilatta"] or 0) > 0.005])
    nakilde = sirala([bekleyen(k, "nakilde", "nakil") for k in kalemler if (k["nakilde"] or 0) > 0.005])

    kontrol = []
    for k in kalemler:
        temel = {"id": k["id"], "irsaliye_id": k["irsaliye_id"], "irsaliye_no": k["irsaliye_no"], "firma": k["firma"],
                 "giris_tarihi": k["giris_tarihi"], "malzeme_tanim": k["malzeme_tanim"], "sap_kodu": k["sap_kodu"], "durum": k["durum"], "giris": k["giris"]}
        kesin_durum = k["durum"] in ("Çıkış", "Çıkış (A63)")
        if k["miktari_eksik"]:
            kontrol.append(dict(temel, tip="miktar_eksik", aciklama="Miktarı girilmemiş hareket var", kg=None))
        elif k["kaynak"] == "hareket" and k["kalan"] is not None and k["kalan"] < -0.005:
            kontrol.append(dict(temel, tip="fazla_cikis", aciklama=f"Kesin çıkan girişten {abs(k['kalan']):.2f} fazla", kg=round(-k["kalan"], 3)))
        elif kesin_durum and k["kaynak"] == "hareket" and k["kalan"] is not None and k["kalan"] > 0.005:
            kontrol.append(dict(temel, tip="cikis_kalan", aciklama=f"Durum Çıkış ama {k['kalan']:.2f} henüz kesin çıkmamış", kg=round(k["kalan"], 3)))
        elif kesin_durum and not k["hareket_sayisi"]:
            kontrol.append(dict(temel, tip="hareketsiz_cikis", aciklama="Durum Çıkış ama çıkış irsaliyesi girilmemiş", kg=k["giris"]))
    kontrol.sort(key=lambda x: (x["tip"], x["irsaliye_no"] or ""))
    kontrol_sayilari = {}
    for x in kontrol:
        kontrol_sayilari[x["tip"]] = kontrol_sayilari.get(x["tip"], 0) + 1
    return {"tadilatta": tadilatta, "nakilde": nakilde, "kontrol": kontrol, "kontrol_sayilari": kontrol_sayilari}


@fason_bp.route("/api/fason/rapor/konum", methods=["GET"])
def api_fason_rapor_konum():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        return jsonify(_rapor_konum_ozet(_kalem_konumlari(conn)))
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/hareket-trend", methods=["GET"])
def api_fason_rapor_hareket_trend():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        return jsonify(_rapor_hareket_trend(conn))
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/takip", methods=["GET"])
def api_fason_rapor_takip():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        t = _rapor_takip(conn, _kalem_konumlari(conn))
        limit = 500
        return jsonify({"tadilatta": t["tadilatta"][:limit], "nakilde": t["nakilde"][:limit], "kontrol": t["kontrol"][:limit],
                        "sayilar": {"tadilatta": len(t["tadilatta"]), "nakilde": len(t["nakilde"]), "kontrol": len(t["kontrol"])},
                        "kontrol_sayilari": t["kontrol_sayilari"]})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/rapor/excel", methods=["GET"])
def api_fason_rapor_excel():
    """Raporun filtreli halini çok sayfalı Excel'e aktarır: Özet, Firma Bazlı, Tadilatta, Akkuyu'da, Kontrol, Aylık Hareket."""
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        conn = get_db()
        try:
            kalemler = _kalem_konumlari(conn)
            konum = _rapor_konum_ozet(kalemler)
            takip = _rapor_takip(conn, kalemler)
            trend = _rapor_hareket_trend(conn)
            firma_ad = ""
            firma_arg = (request.args.get("firma") or "").strip()
            if firma_arg == "__yok__":
                firma_ad = "Firma Yok"
            elif firma_arg:
                r = conn.execute("SELECT ad FROM fason_firma WHERE id = ?", (firma_arg,)).fetchone()
                firma_ad = r["ad"] if r else ""
        finally:
            conn.close()

        b, s = _rapor_tarih_arg("baslangic"), _rapor_tarih_arg("bitis")
        filtre_metni = "Filtre: " + ", ".join(
            [x for x in [f"Firma: {firma_ad}" if firma_ad else "", f"Giriş tarihi ≥ {b}" if b else "",
                         f"Giriş tarihi ≤ {s}" if s else ""] if x] or ["Yok (tüm kayıtlar)"]) + \
            f" · Oluşturma: {datetime.now().strftime('%d.%m.%Y %H:%M')}"

        baslik_font = Font(bold=True, color="FFFFFF")
        baslik_fill = PatternFill("solid", fgColor="374151")
        sayi_fmt = "#,##0.00"

        wb = Workbook()

        def _excel_tarih(iso):
            try:
                return datetime.strptime(iso[:10], "%Y-%m-%d") if iso else None
            except ValueError:
                return iso

        def sayfa(ad, basliklar, satirlar, genislikler, sayisal=(), tarih=()):
            ws = wb.create_sheet(ad) if wb.sheetnames != ["Sheet"] else wb.active
            if ws.title == "Sheet":
                ws.title = ad
            ws.cell(row=1, column=1, value=filtre_metni).font = Font(italic=True, color="6B7280", size=9)
            for i, b_ in enumerate(basliklar, start=1):
                c = ws.cell(row=2, column=i, value=b_)
                c.font, c.fill = baslik_font, baslik_fill
                c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for r_i, satir in enumerate(satirlar, start=3):
                for c_i, v in enumerate(satir, start=1):
                    c = ws.cell(row=r_i, column=c_i, value=v)
                    if c_i in sayisal and isinstance(v, (int, float)):
                        c.number_format = sayi_fmt
                    elif c_i in tarih and isinstance(v, datetime):
                        c.number_format = "DD.MM.YYYY"
            for i, w in enumerate(genislikler, start=1):
                ws.column_dimensions[get_column_letter(i)].width = w
            ws.freeze_panes = "A3"
            if satirlar:
                ws.auto_filter.ref = f"A2:{get_column_letter(len(basliklar))}{2 + len(satirlar)}"
            return ws

        t = konum["toplam"]
        sayfa("Özet", ["Gösterge", "Değer (kg)"], [
            ["SAP Giriş", t["giris"]], ["Depoda", t["depoda"]], ["Tadilatta", t["tadilatta"]],
            ["Akkuyu'da (nakil)", t["nakilde"]], ["Kesin Çıkan (Çıkış + A63)", t["kesin"]],
            ["Henüz kesin çıkmamış (Giriş − Kesin Çıkan)", t["giris"] - t["kesin"]],
            ["Toplam Tutar ($)", t["tutar"]],
            ["İrsaliye sayısı", konum["irsaliye_sayisi"]], ["Kalem sayısı", konum["kalem_sayisi"]],
            ["Konumu durumdan tahmin edilen kalem", konum["durumdan_tahmini"]],
            ["Miktarı girilmemiş hareketi olan kalem", konum["miktari_eksik"]],
            ["ZMM068 belge (giriş) tarihi olmayan kalem", konum["giris_tarihsiz"]],
            ["Tarihi girilmemiş hareket", trend["tarihsiz_hareket"]],
        ], [46, 18], sayisal=(2,))

        sayfa("Firma Bazlı",
              ["Firma", "İrsaliye", "Kalem", "SAP Giriş", "Depoda", "Tadilatta", "Akkuyu'da", "Kesin Çıkan", "Kalan", "Çıkış %", "Tutar ($)"],
              [[f["firma"], f["irsaliye"], f["kalem"], f["giris"], f["depoda"], f["tadilatta"], f["nakilde"],
                f["kesin"], f["kalan"], f["cikis_orani"], f["tutar"]] for f in konum["firmalar"]],
              [22, 10, 10, 14, 14, 14, 14, 14, 14, 10, 14], sayisal=(4, 5, 6, 7, 8, 9, 11))

        bekleyen_baslik = ["Fason İrsaliye", "Giriş Tarihi (ZMM068)", "Firma", "Malzeme Tanım", "SAP Kodu", "Miktar (kg)", "Gönderim İrsaliyesi", "Gönderim Tarihi", "Gün"]
        bekleyen_satir = lambda l: [[x["irsaliye_no"], _excel_tarih(x["giris_tarihi"]), x["firma"], x["malzeme_tanim"], x["sap_kodu"], x["kg"],
                                     x["gonderim_irsaliye"], _excel_tarih(x["gonderim_tarihi"]), x["gun"]] for x in l]
        sayfa("Tadilatta", bekleyen_baslik, bekleyen_satir(takip["tadilatta"]), [20, 14, 18, 44, 14, 13, 20, 14, 8], sayisal=(6,), tarih=(2, 8))
        sayfa("Akkuyu'da", bekleyen_baslik, bekleyen_satir(takip["nakilde"]), [20, 14, 18, 44, 14, 13, 20, 14, 8], sayisal=(6,), tarih=(2, 8))

        kontrol_etiket = {"miktar_eksik": "Miktar girilmemiş", "fazla_cikis": "Girişten fazla çıkış",
                          "cikis_kalan": "Çıkış ama kalan var", "hareketsiz_cikis": "Çıkış ama irsaliyesi yok"}
        sayfa("Kontrol", ["Sorun", "Açıklama", "Fason İrsaliye", "Giriş Tarihi (ZMM068)", "Firma", "Malzeme Tanım", "SAP Kodu", "Durum", "SAP Giriş", "Miktar (kg)"],
              [[kontrol_etiket.get(x["tip"], x["tip"]), x["aciklama"], x["irsaliye_no"], _excel_tarih(x["giris_tarihi"]), x["firma"],
                x["malzeme_tanim"], x["sap_kodu"], x["durum"], x["giris"], x["kg"]] for x in takip["kontrol"]],
              [22, 42, 20, 14, 18, 44, 14, 12, 12, 12], sayisal=(9, 10), tarih=(4,))

        sr = trend["seriler"]
        sayfa("Aylık Hareket", ["Ay", "Giriş", "Kesin Çıkış", "Tadilat Gönderimi", "Tadilattan Dönüş", "Nakil"],
              [[a, sr["giris"][i], sr["kesin"][i], sr["tadilat_gidis"][i], sr["tadilat_donus"][i], sr["nakil"][i]]
               for i, a in enumerate(trend["aylar"])],
              [10, 14, 14, 18, 18, 14], sayisal=(2, 3, 4, 5, 6))

        klasor = os.path.join(_fason_export_klasor(), "exports", "fason_rapor")
        os.makedirs(klasor, exist_ok=True)
        dosya_adi = f"fason_rapor_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        wb.save(os.path.join(klasor, dosya_adi))
        try:
            os.startfile(klasor)
        except Exception:
            pass
        return jsonify({"durum": "ok", "dosya": dosya_adi, "mesaj": f"Rapor Excel'e aktarıldı ({dosya_adi})"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


def fasoncu_insight_uret():
    """
    Fasoncu rolü için 'İçgörüler' panelinde gösterilecek, gerçek veriye
    dayalı insight listesini üretir. Her insight: {tip, baslik, detay, link}
    """
    insightler = []
    try:
        conn = get_db()

        # 1) Firma bazında "durumu belirlenmemiş" kalem sayısı (eşik: 100)
        satirlar = conn.execute("""
            SELECT COALESCE(f.ad, 'Firma atanmamış') AS firma_ad,
                   COUNT(*) AS kalem_sayisi,
                   COUNT(DISTINCT i.id) AS irsaliye_sayisi
            FROM fason_kalem k
            JOIN fason_irsaliye i ON k.irsaliye_id = i.id
            LEFT JOIN fason_firma f ON i.firma_id = f.id
            WHERE (k.durum IS NULL OR k.durum = '')
            GROUP BY i.firma_id
            HAVING kalem_sayisi > 100
            ORDER BY kalem_sayisi DESC
            LIMIT 3
        """).fetchall()
        for s in satirlar:
            insightler.append({
                "tip": "uyari",
                "baslik": f"{s['firma_ad']} bekliyor",
                "detay": f"{s['firma_ad']} firmasının {s['irsaliye_sayisi']} irsaliyede toplam {s['kalem_sayisi']} kalemi hâlâ 'Belirlenmedi' durumunda.",
                "link": "/fason"
            })

        # 2) "Bugün ilgilenmen için harika bir gün" — az kalanı olan, çabucak bitirilebilecek firma
        hizli = conn.execute("""
            SELECT COALESCE(f.ad, 'Firma atanmamış') AS firma_ad,
                   COUNT(*) AS kalem_sayisi
            FROM fason_kalem k
            JOIN fason_irsaliye i ON k.irsaliye_id = i.id
            LEFT JOIN fason_firma f ON i.firma_id = f.id
            WHERE (k.durum IS NULL OR k.durum = '')
            GROUP BY i.firma_id
            HAVING kalem_sayisi BETWEEN 1 AND 10
            ORDER BY kalem_sayisi ASC
            LIMIT 1
        """).fetchone()
        if hizli:
            insightler.append({
                "tip": "basari",
                "baslik": "Hızlı bir kazanç seni bekliyor",
                "detay": f"{hizli['firma_ad']} ile ilgilenmen için harika bir gün — sadece {hizli['kalem_sayisi']} kalem kaldı, hemen bitirebilirsin.",
                "link": "/fason"
            })

        # 3) En eski, hâlâ belirlenmemiş irsaliye (30 günden eskiyse uyar)
        eski = conn.execute("""
            SELECT COALESCE(f.ad, 'Firma atanmamış') AS firma_ad, i.irsaliye_no, i.girilme_tarihi,
                   CAST(julianday('now','localtime') - julianday(i.girilme_tarihi) AS INTEGER) AS gun_sayisi
            FROM fason_irsaliye i
            JOIN fason_kalem k ON k.irsaliye_id = i.id
            LEFT JOIN fason_firma f ON i.firma_id = f.id
            WHERE (k.durum IS NULL OR k.durum = '')
            GROUP BY i.id
            ORDER BY i.girilme_tarihi ASC
            LIMIT 1
        """).fetchone()
        if eski and eski["gun_sayisi"] and eski["gun_sayisi"] > 30:
            insightler.append({
                "tip": "uyari",
                "baslik": "Uzun süredir bekleyen bir irsaliye var",
                "detay": f"{eski['irsaliye_no']} ({eski['firma_ad']}) {eski['gun_sayisi']} gündür 'Belirlenmedi' durumunda bekliyor.",
                "link": "/fason"
            })

        conn.close()
    except Exception as e:
        insightler.append({"tip": "bilgi", "baslik": "İçgörüler hesaplanamadı", "detay": str(e), "link": None})

    if not insightler:
        insightler.append({
            "tip": "basari",
            "baslik": "Her şey yolunda",
            "detay": "Şu an dikkat gerektiren bir durum yok — bekleyen kalem bulunmuyor.",
            "link": None
        })
    return insightler
