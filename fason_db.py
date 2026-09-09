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
    # admin kullanıcısı hızlı yerel veritabanını kullanır (senkronizasyon onun elinde).
    # Diğer herkes doğrudan K:'deki (admin'in yedeklediği) veritabanını okur/yazar.
    if session.get("kullanici") == "admin":
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
    ]
    eksik_kalem = [(ad, tip) for ad, tip in ek_kolonlar if ad not in mevcut_kalem]
    if eksik_kalem:
        conn.executescript(";\n".join(
            f"ALTER TABLE fason_kalem ADD COLUMN {ad} {tip}" for ad, tip in eksik_kalem
        ) + ";")
    # 'durum' kolonunda index yoksa aşağıdaki UPDATE (ve durum'a göre filtreleyen her sorgu)
    # tüm tabloyu tarar — ağ sürücüsünde bu saniyeler sürebilir. Index'i garantiye al.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kalem_durum ON fason_kalem(durum)")
    print(f"[FASON-TANI]   kalem alter + durum index (varsa) tamam: {_t.time()-_b:.2f} sn", flush=True)
    # Stok miktarı hiç girilmemiş kalemlerde "Stok" durumu otomatik atanmış olabilir — temizle.
    # Bu tek seferlik bir veri temizliği; 'durum' kolonu varsayılan olarak 'Stok' geldiği için
    # index bu sorguyu ayırt edemiyor (neredeyse tüm satırlar eşleşiyor) — ağdaki büyüyen
    # ortak tabloya karşı her açılışta taratmanın maliyeti çok yüksek. Sadece yerelde çalıştır.
    if yerel:
        try:
            conn.execute("UPDATE fason_kalem SET durum = '' WHERE durum = 'Stok' AND stok_miktari IS NULL")
        except Exception:
            pass
    print(f"[FASON-TANI]   update tamam: {_t.time()-_b:.2f} sn", flush=True)

    _fason_kalem_migration(conn)
    print(f"[FASON-TANI]   _fason_kalem_migration tamam: {_t.time()-_b:.2f} sn", flush=True)


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


def _init_fason_db_at(db_yol):
    import time as _t
    _b = _t.time()
    def _tan(etiket):
        print(f"[FASON-TANI] {etiket} ({db_yol}): {_t.time()-_b:.2f} sn", flush=True)
    conn = _connect_db(db_yol)
    _tan("_connect_db tamam")
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
        """)
        _tan("1. executescript (4 tablo+1 index) tamam")

        _migrate_ek_kolonlar(conn, yerel=(db_yol == DB_YOL))
        _tan("_migrate_ek_kolonlar tamam")

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

        conn.commit()
        _tan("commit tamam")
        print(f"[Fason DB] Tablolar hazir: {db_yol}")
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
                k.toplam_fiyat_sum, k.kalem_sayisi,
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
                       CASE WHEN COUNT(DISTINCT durum) = 1 THEN MAX(durum) ELSE 'Kısmi Çıkış' END AS durum_hesaplanan
                FROM fason_kalem GROUP BY irsaliye_id
            ) k ON k.irsaliye_id = i.id
            ORDER BY i.id DESC
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


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
        rows = conn.execute(
            "SELECT * FROM fason_kalem WHERE irsaliye_id = ? ORDER BY id", (irs_id,)
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@fason_bp.route("/api/fason/kalem-ekle/<int:irs_id>", methods=["POST"])
def api_fason_kalem_ekle(irs_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
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
            (d.get("durum") or "").strip(), (d.get("cikis_irsaliye_no") or "").strip()
        ))
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
    d = request.get_json() or {}

    def sayi(v):
        if v in (None, ""): return None
        try: return float(v)
        except Exception: return None

    alanlar = ["malzeme_tanim", "sap_kodu", "irsaliye_tarihi", "stok_miktari", "giris_miktari",
               "otis_stok", "otis_giris", "toplam_fiyat", "para_birimi", "birim_fiyat",
               "belge_tarihi", "fark_sebebi", "durum", "cikis_irsaliye_no"]
    sayisal = {"stok_miktari", "giris_miktari", "otis_stok", "otis_giris", "toplam_fiyat", "birim_fiyat"}

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
        if "durum" in d and d["durum"] not in ("Stok", "Çıkış", "Tadilat", "Nakil", "Aspro"):
            return jsonify({"durum": "hata", "mesaj": "Geçersiz durum değeri"}), 400          
        if not set_parts:
            return jsonify({"durum": "hata", "mesaj": "Güncellenecek alan yok"}), 400
        vals.append(kalem_id)
        conn.execute(f"UPDATE fason_kalem SET {', '.join(set_parts)} WHERE id = ?", vals)
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
    conn = get_db()
    try:
        row = conn.execute("SELECT malzeme_tanim FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
        if not row:
            return jsonify({"durum": "hata", "mesaj": "Bulunamadı"}), 404
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
# KAYIT DÜZENLEME (tüm alanlar) — sadece admin kullanıcısı
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
# İrsaliye no'ya göre eşleşen satırları kalem olarak işler
# ═════════════════════════════════════════════════

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
        eslesmeyen_irsaliye = 0
        yeni_kalem = 0
        guncellenen_kalem = 0
        atlanan = []

        # irsaliye_no -> irsaliye_id cache
        irsaliye_cache = {}
        temizlenen_genel_kalem = set()

        try:
            for row in rows_iter:
                toplam_satir += 1
                irsaliye_no = str(al(row, c_referans) or "").strip()
                malzeme_tanim = str(al(row, c_kisa_metin) or "").strip()
                if not irsaliye_no or not malzeme_tanim:
                    continue

                if irsaliye_no not in irsaliye_cache:
                    r = conn.execute(
                        "SELECT id FROM fason_irsaliye WHERE irsaliye_no = ?", (irsaliye_no,)
                    ).fetchone()
                    irsaliye_cache[irsaliye_no] = r["id"] if r else None

                irs_id = irsaliye_cache[irsaliye_no]
                if irs_id is None:
                    eslesmeyen_irsaliye += 1
                    atlanan.append(irsaliye_no)
                    continue

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

                if mevcut:
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

            eslesen_irsaliye = len(set(k for k, v in irsaliye_cache.items() if v is not None))
            conn.commit()
        finally:
            conn.close()

        log_kaydet(
            "ZMM068 Import",
            f"{dosya.filename}: {yeni_kalem} yeni kalem, {guncellenen_kalem} güncellendi, "
            f"{eslesen_irsaliye} irsaliye eşleşti, {eslesmeyen_irsaliye} satır eşleşmedi",
            None, dosya.filename
        )

        return jsonify({
            "durum": "ok",
            "toplam_satir": toplam_satir,
            "eslesen_irsaliye": eslesen_irsaliye,
            "eslesmeyen_satir": eslesmeyen_irsaliye,
            "yeni_kalem": yeni_kalem,
            "guncellenen_kalem": guncellenen_kalem,
            "atlanan_irsaliyeler": sorted(set(atlanan))[:30],
            "mesaj": f"{yeni_kalem} yeni kalem, {guncellenen_kalem} güncellendi ({eslesmeyen_irsaliye} satır sistemde olmayan irsaliyeye ait, atlandı)"
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

        irsaliye_cache = {}       # irsaliye_no -> irs_id (None ise sistemde yok)
        kalem_cache = {}          # irs_id -> [fason_kalem satırları] (irsaliye başına bir kere çekilir)
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

                if irsaliye_no not in irsaliye_cache:
                    r = conn.execute(
                        "SELECT id FROM fason_irsaliye WHERE irsaliye_no = ?", (irsaliye_no,)
                    ).fetchone()
                    irsaliye_cache[irsaliye_no] = r["id"] if r else None

                irs_id = irsaliye_cache[irsaliye_no]
                if irs_id is None:
                    eslesmeyen_irsaliye += 1
                    atlanan_irsaliyeler.append(irsaliye_no)
                    continue

                # Gerçek malzeme verisi geldiğinde, migration'dan kalan boş "Genel Kalem" plasholder'ını sil
                if irs_id not in temizlenen_genel_kalem:
                    conn.execute("""
                        DELETE FROM fason_kalem
                        WHERE irsaliye_id = ? AND malzeme_tanim = 'Genel Kalem' AND (sap_kodu IS NULL OR sap_kodu = '')
                    """, (irs_id,))
                    temizlenen_genel_kalem.add(irs_id)

                if irs_id not in kalem_cache:
                    kalem_cache[irs_id] = conn.execute(
                        "SELECT * FROM fason_kalem WHERE irsaliye_id = ?", (irs_id,)
                    ).fetchall()

                kalemler = kalem_cache[irs_id]

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
                if sap_kodu_otis:
                    sap_adaylari = [k for k in kalemler if k["otis_malzeme_tanim"] is None and k["sap_kodu"] == sap_kodu_otis]
                    if len(sap_adaylari) == 1:
                        aday = sap_adaylari[0]

                # 3) %98+ METİN BENZERLİĞİ İLE OTOMATİK EŞLEŞTİRME
                if aday is None:
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
                            otis_stok_onceki = ?, otis_stok_son_degisim_tarihi = ?
                        WHERE id = ?
                    """, (malzeme_tanim, sap_kodu_otis, otis_stok, otis_giris, eski_stok,
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S") if eski_stok is not None else aday["otis_stok_son_degisim_tarihi"],
                          aday["id"]))
                    otomatik_eslesen += 1
                    # cache'i güncelle ki aynı import içinde tekrar eşleşmesin
                    kalem_cache[irs_id] = [
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

        conn.execute("""
            UPDATE fason_kalem
            SET otis_malzeme_tanim = ?, otis_sap_kodu = ?, otis_stok = ?, otis_giris = ?
            WHERE id = ?
        """, (b["malzeme_tanim"], b["sap_kodu"], b["otis_stok"], b["otis_giris"], kalem_id))
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
                (irsaliye_id, malzeme_tanim, otis_malzeme_tanim, otis_sap_kodu, otis_stok, otis_giris, durum)
            VALUES (?, ?, ?, ?, ?, ?, '')
        """, (b["irsaliye_id"], b["malzeme_tanim"], b["malzeme_tanim"], b["sap_kodu"], b["otis_stok"], b["otis_giris"]))
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

@fason_bp.route("/api/fason/tum-kalemler", methods=["GET"])
def api_fason_tum_kalemler():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    ara = request.args.get("ara", "").strip()
    cikis_irs_ara = request.args.get("cikis_irs", "").strip()

    sorgu = """
        SELECT k.id, k.malzeme_tanim, k.sap_kodu, k.stok_miktari, k.giris_miktari,
               k.otis_stok, k.otis_giris, k.fark_sebebi, k.durum, k.cikis_irsaliye_no,
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
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()

@fason_bp.route("/api/fason/genel-kalem-temizle", methods=["POST"])
def api_fason_genel_kalem_temizle():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("kullanici") != "admin":
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

        ws.merge_cells("H1:J1")
        ws["H1"] = "MB52'DEN KONTROL EDİP DOLDURUN"
        ws["H1"].font = Font(bold=True, color="FFFFFF", size=10)
        ws["H1"].fill = edit_fill
        ws["H1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22

        basliklar = [
            ("Kalem ID", "kilit"), ("Malzeme Tanım", "kilit"), ("SAP Kodu", "kilit"),
            ("OTIS Giriş", "kilit"), ("SAP Giriş", "kilit"), ("OTIS Stok", "kilit"), ("Mevcut Stok", "kilit"),
            ("Yeni Stok", "edit"), ("Çıkış İrsaliyesi", "edit"), ("Yeni Durum", "edit"),
        ]
        for col_idx, (baslik, tip) in enumerate(basliklar, start=1):
            cell = ws.cell(row=2, column=col_idx, value=baslik)
            cell.font = h_font
            cell.fill = kilit_fill if tip == "kilit" else edit_fill
            cell.alignment = h_align
            cell.border = border
        ws.row_dimensions[2].height = 28

        for i, w in enumerate([10, 45, 16, 12, 12, 12, 12, 12, 18, 14], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        thin_border = Border(
            left=Side(style="thin", color="E5E7EB"), right=Side(style="thin", color="E5E7EB"),
            top=Side(style="thin", color="E5E7EB"), bottom=Side(style="thin", color="E5E7EB")
        )
        for idx, k in enumerate(kalemler, start=3):
            values = [
                k["id"], k["malzeme_tanim"], k["sap_kodu"] or "",
                k["otis_giris"], k["giris_miktari"], k["otis_stok"], k["stok_miktari"],
                None, k["cikis_irsaliye_no"] or "", k["durum"] or "Stok",
            ]
            for c_idx, v in enumerate(values, start=1):
                cell = ws.cell(row=idx, column=c_idx, value=v)
                cell.border = thin_border
                if c_idx in (1, 4, 5, 6, 7, 8):
                    cell.alignment = Alignment(horizontal="right")
                    if c_idx in (4, 5, 6, 7, 8) and v is not None:
                        cell.number_format = "#,##0.00"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
            if idx % 2 == 0:
                for c_idx in range(1, 11):
                    ws.cell(row=idx, column=c_idx).fill = PatternFill("solid", fgColor="F9FAFB")

        durum_dv = DataValidation(type="list", formula1='"Stok,Çıkış,Tadilat,Nakil,Aspro"', allow_blank=True)
        ws.add_data_validation(durum_dv)
        durum_dv.add(f"J3:J{2 + len(kalemler)}")

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

                mevcut = conn.execute("SELECT id, malzeme_tanim, stok_miktari, durum, cikis_irsaliye_no FROM fason_kalem WHERE id = ?", (kalem_id,)).fetchone()
                if not mevcut:
                    continue

                # Otomatik durum kuralı: Excel'de "Yeni Durum" elle girilmediyse,
                # çıkış irsaliyesi girildiyse ya da stok 0'landıysa otomatik "Çıkış" say
                yeni_durum = mevcut["durum"] or "Stok"
                if durum_deger in ("Stok", "Çıkış", "Tadilat", "Nakil", "Aspro"):
                    yeni_durum = durum_deger
                elif cikis_irs_deger:
                    yeni_durum = "Çıkış"
                elif yeni_stok is not None and abs(yeni_stok) < 0.005:
                    yeni_durum = "Çıkış"

                yeni_cikis_irs = cikis_irs_deger or (mevcut["cikis_irsaliye_no"] or "")

                degisti = (mevcut["stok_miktari"] != yeni_stok) or (mevcut["durum"] != yeni_durum) or ((mevcut["cikis_irsaliye_no"] or "") != yeni_cikis_irs)
                if not degisti:
                    continue

                conn.execute(
                    "UPDATE fason_kalem SET stok_miktari = ?, durum = ?, cikis_irsaliye_no = ? WHERE id = ?",
                    (yeni_stok, yeni_durum, yeni_cikis_irs, kalem_id)
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
            "mesaj": f"{guncellenen} kalemin stoğu güncellendi"
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500

@fason_bp.route("/api/fason/db-senkronize-et", methods=["POST"])
def api_fason_db_senkronize_et():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("kullanici") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Sadece admin"}), 403
    try:
        if not os.path.isdir(ORTAK_KLASOR):
            return jsonify({"durum": "hata", "mesaj": "K: sürücüsüne şu an erişilemiyor"}), 500

        hedef_yol = os.path.join(ORTAK_KLASOR, "fason.db")
        kaynak = sqlite3.connect(DB_YOL)
        hedef = sqlite3.connect(hedef_yol)
        kaynak.backup(hedef)
        hedef.close()
        kaynak.close()

        return jsonify({
            "durum": "ok",
            "mesaj": "fason.db K: sürücüsüne gönderildi (üzerine yazıldı)"
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500