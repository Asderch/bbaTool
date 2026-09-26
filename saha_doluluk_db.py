# -*- coding: utf-8 -*-
"""
saha_doluluk_db.py — BBA Saha Doluluk Modülü
Yeşilovacık sahasındaki YSL lokasyonlarının (1-A, 2-F, 3-K vb.) doluluk
yüzdesini takip etmek için. Ortofoto üzerine çizilen poligonlarla lokasyonlar
seçilebilir/tıklanabilir hale getirilir.

Doluluk % şimdilik MANUEL girişle tutuluyor (bkz. api_saha_doluluk_guncelle).
İleride kapasite (m² ya da ton) + kullanım miktarına dayalı otomatik hesaba
çevrilmesi planlanıyor — o zaman fason_kalem'deki gibi bir "kapasite" kolonu
eklenip % otomatik hesaplanacak. Şimdilik bu alan boş/0 bırakılıyor.
"""

import os
import sys
import json
import sqlite3
from datetime import datetime
from flask import Blueprint, request, jsonify, session

saha_bp = Blueprint("saha_doluluk", __name__)

# Diğer modüllerle (fason_db.py) aynı ortak klasör — bu app'in paylaşılan
# ağ konumu. Aynı admin/yerel-db + K: yedekleme deseni burada da kullanılıyor.
ORTAK_KLASOR = r"K:\Warehouse\Yeşilovacık\12_Paylaşım Klasörü\01-BBA\bba-tool"


def _db_klasor_bul():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DB_KLASOR = _db_klasor_bul()
DB_YOL = os.path.join(DB_KLASOR, "saha_doluluk.db")


def _connect_db(db_yol):
    os.makedirs(os.path.dirname(db_yol), exist_ok=True)
    conn = sqlite3.connect(db_yol, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ag_yolu = os.path.isdir(ORTAK_KLASOR) and os.path.normcase(os.path.abspath(db_yol)).startswith(os.path.normcase(os.path.abspath(ORTAK_KLASOR)))
    if ag_yolu:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
    else:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _aktif_db_yolu():
    # Fason/hammadde modüllerinin aksine burada admin/yerel ayrımı YOK —
    # herkes (admin dahil) doğrudan K:'deki ortak veritabanını kullanır.
    # K: şu an erişilemiyorsa (ağ koptu vb.) çökmemek için yerel dosyaya düşülür.
    if os.path.isdir(ORTAK_KLASOR):
        return os.path.join(ORTAK_KLASOR, "saha_doluluk.db")
    return DB_YOL


def get_db():
    return _connect_db(_aktif_db_yolu())


# ═════════════════════════════════════════════════
# BAŞLANGIÇ YERLEŞİMİ (TASLAK)
# Bu koordinatlar ortofoto üzerinde YSL lokasyonlarının KABA/tahmini
# konumları — gerçek sınırlarla birebir eşleşmiyor. "Alanları Düzenle"
# modunda köşe noktaları sürüklenerek gerçek sınırlara oturtulmalı.
# poligon: resmin genişlik/yüksekliğine göre ORANSAL (0-1 arası) [x,y] noktaları.
# ═════════════════════════════════════════════════
_BASLANGIC_LOKASYONLAR = [
    {"kod": "YSL 1-F", "saha_no": 1, "poligon": [[0.046, 0.4028], [0.198, 0.4028], [0.198, 0.468], [0.046, 0.468]]},
    {"kod": "YSL 1-E", "saha_no": 1, "poligon": [[0.046, 0.4737], [0.198, 0.4737], [0.198, 0.5388], [0.046, 0.5388]]},
    {"kod": "YSL 1-D", "saha_no": 1, "poligon": [[0.046, 0.5445], [0.198, 0.5445], [0.198, 0.6097], [0.046, 0.6097]]},
    {"kod": "YSL 1-C", "saha_no": 1, "poligon": [[0.046, 0.6153], [0.198, 0.6153], [0.198, 0.6805], [0.046, 0.6805]]},
    {"kod": "YSL 1-B", "saha_no": 1, "poligon": [[0.046, 0.6862], [0.198, 0.6862], [0.198, 0.7513], [0.046, 0.7513]]},
    {"kod": "YSL 1-A", "saha_no": 1, "poligon": [[0.046, 0.757], [0.198, 0.757], [0.198, 0.8222], [0.046, 0.8222]]},

    {"kod": "YSL 2-A", "saha_no": 2, "poligon": [[0.353, 0.2864], [0.512, 0.2864], [0.512, 0.3247], [0.353, 0.3247]]},
    {"kod": "YSL 2-B", "saha_no": 2, "poligon": [[0.353, 0.3324], [0.512, 0.3324], [0.512, 0.3708], [0.353, 0.3708]]},
    {"kod": "YSL 2-C", "saha_no": 2, "poligon": [[0.353, 0.3785], [0.512, 0.3785], [0.512, 0.4168], [0.353, 0.4168]]},
    {"kod": "YSL 2-D", "saha_no": 2, "poligon": [[0.353, 0.4246], [0.512, 0.4246], [0.512, 0.4629], [0.353, 0.4629]]},
    {"kod": "YSL 2-E", "saha_no": 2, "poligon": [[0.353, 0.4706], [0.512, 0.4706], [0.512, 0.5089], [0.353, 0.5089]]},
    {"kod": "YSL 2-F", "saha_no": 2, "poligon": [[0.353, 0.5167], [0.512, 0.5167], [0.512, 0.555], [0.353, 0.555]]},
    {"kod": "YSL 2-G", "saha_no": 2, "poligon": [[0.353, 0.5628], [0.512, 0.5628], [0.512, 0.6011], [0.353, 0.6011]]},
    {"kod": "YSL 2-H", "saha_no": 2, "poligon": [[0.353, 0.6089], [0.512, 0.6089], [0.512, 0.6472], [0.353, 0.6472]]},
    {"kod": "YSL 2-I", "saha_no": 2, "poligon": [[0.353, 0.6549], [0.512, 0.6549], [0.512, 0.6932], [0.353, 0.6932]]},
    {"kod": "YSL 2-F-A", "saha_no": 2, "poligon": [[0.3583, 0.62], [0.4113, 0.62], [0.4113, 0.7], [0.3583, 0.7]]},
    {"kod": "YSL 2-F-B", "saha_no": 2, "poligon": [[0.4166, 0.62], [0.4696, 0.62], [0.4696, 0.7], [0.4166, 0.7]]},
    {"kod": "YSL 2-F-C", "saha_no": 2, "poligon": [[0.475, 0.62], [0.528, 0.62], [0.528, 0.7], [0.475, 0.7]]},

    {"kod": "YSL 3-N", "saha_no": 3, "poligon": [[0.503, 0.1798], [0.644, 0.1798], [0.644, 0.2278], [0.503, 0.2278]]},
    {"kod": "YSL 3-M", "saha_no": 3, "poligon": [[0.503, 0.2318], [0.644, 0.2318], [0.644, 0.2798], [0.503, 0.2798]]},
    {"kod": "YSL 3-L", "saha_no": 3, "poligon": [[0.503, 0.2838], [0.644, 0.2838], [0.644, 0.3318], [0.503, 0.3318]]},
    {"kod": "YSL 3-K", "saha_no": 3, "poligon": [[0.503, 0.3358], [0.644, 0.3358], [0.644, 0.3838], [0.503, 0.3838]]},
    {"kod": "YSL 3-J", "saha_no": 3, "poligon": [[0.503, 0.3878], [0.644, 0.3878], [0.644, 0.4358], [0.503, 0.4358]]},
    {"kod": "YSL 3-I", "saha_no": 3, "poligon": [[0.503, 0.4398], [0.644, 0.4398], [0.644, 0.4878], [0.503, 0.4878]]},
    {"kod": "YSL 3-H", "saha_no": 3, "poligon": [[0.503, 0.4918], [0.644, 0.4918], [0.644, 0.5398], [0.503, 0.5398]]},
    {"kod": "YSL 3-G", "saha_no": 3, "poligon": [[0.503, 0.5438], [0.644, 0.5438], [0.644, 0.5918], [0.503, 0.5918]]},
    {"kod": "YSL 3-F", "saha_no": 3, "poligon": [[0.503, 0.5958], [0.644, 0.5958], [0.644, 0.6438], [0.503, 0.6438]]},
    {"kod": "YSL 3-F-A", "saha_no": 3, "poligon": [[0.503, 0.6478], [0.644, 0.6478], [0.644, 0.6958], [0.503, 0.6958]]},
    {"kod": "YSL 3-F-V", "saha_no": 3, "poligon": [[0.531, 0.5788], [0.704, 0.5788], [0.704, 0.6188], [0.531, 0.6188]]},
    {"kod": "YSL 3-C", "saha_no": 3, "poligon": [[0.531, 0.6228], [0.704, 0.6228], [0.704, 0.6628], [0.531, 0.6628]]},
    {"kod": "YSL 3-B", "saha_no": 3, "poligon": [[0.531, 0.6668], [0.704, 0.6668], [0.704, 0.7068], [0.531, 0.7068]]},
    {"kod": "YSL 3-A", "saha_no": 3, "poligon": [[0.531, 0.7108], [0.704, 0.7108], [0.704, 0.7508], [0.531, 0.7508]]},
    {"kod": "YSL 3-F-K-1", "saha_no": 3, "poligon": [[0.531, 0.7548], [0.704, 0.7548], [0.704, 0.7948], [0.531, 0.7948]]},

    {"kod": "YSL 4-D", "saha_no": 4, "poligon": [[0.6963, 0.375], [0.8205, 0.375], [0.8205, 0.45], [0.6963, 0.45]]},
    {"kod": "YSL 4-C", "saha_no": 4, "poligon": [[0.8213, 0.375], [0.9448, 0.375], [0.9448, 0.45], [0.8213, 0.45]]},
    {"kod": "YSL 4-A", "saha_no": 4, "poligon": [[0.6963, 0.4508], [0.8205, 0.4508], [0.8205, 0.525], [0.6963, 0.525]]},
    {"kod": "YSL 4-B", "saha_no": 4, "poligon": [[0.8213, 0.4508], [0.9448, 0.4508], [0.9448, 0.525], [0.8213, 0.525]]},
]


def _init_saha_db_at(db_yol):
    conn = _connect_db(db_yol)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS saha_lokasyon (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                kod                 TEXT NOT NULL UNIQUE,
                saha_no             INTEGER NOT NULL,
                ad                  TEXT DEFAULT '',
                poligon             TEXT NOT NULL DEFAULT '[]',
                doluluk_yuzde       REAL DEFAULT 0,
                doluluk_notu        TEXT DEFAULT '',
                guncelleyen         TEXT DEFAULT '',
                guncelleme_tarihi   TEXT DEFAULT '',
                olusturma_tarihi    TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_saha_lokasyon_saha_no ON saha_lokasyon(saha_no);
        """)
        conn.commit()

        mevcut = conn.execute("SELECT COUNT(*) FROM saha_lokasyon").fetchone()[0]
        if mevcut == 0:
            for lok in _BASLANGIC_LOKASYONLAR:
                conn.execute(
                    "INSERT INTO saha_lokasyon (kod, saha_no, poligon) VALUES (?, ?, ?)",
                    (lok["kod"], lok["saha_no"], json.dumps(lok["poligon"]))
                )
            conn.commit()
            print(f"[Saha Doluluk] {len(_BASLANGIC_LOKASYONLAR)} lokasyon ile başlangıç verisi oluşturuldu.")
    finally:
        conn.close()


def init_saha_doluluk_db():
    _init_saha_db_at(DB_YOL)
    try:
        if os.path.isdir(ORTAK_KLASOR):
            ortak_db_yol = os.path.join(ORTAK_KLASOR, "saha_doluluk.db")
            _init_saha_db_at(ortak_db_yol)
    except Exception as e:
        print(f"[Saha Doluluk] Ortak (K:) veritabanı hazırlanamadı: {e}")


# ═════════════════════════════════════════════════
# API UÇ NOKTALARI
# ═════════════════════════════════════════════════

@saha_bp.route("/api/saha-doluluk/lokasyonlar", methods=["GET"])
def api_saha_doluluk_lokasyonlar():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, kod, saha_no, ad, poligon, doluluk_yuzde, doluluk_notu,
                   guncelleyen, guncelleme_tarihi
            FROM saha_lokasyon
            ORDER BY saha_no, kod
        """).fetchall()
        sonuc = []
        for r in rows:
            d = dict(r)
            try:
                d["poligon"] = json.loads(d["poligon"])
            except Exception:
                d["poligon"] = []
            sonuc.append(d)
        return jsonify(sonuc)
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@saha_bp.route("/api/saha-doluluk/ozet", methods=["GET"])
def api_saha_doluluk_ozet():
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    conn = get_db()
    try:
        toplam = conn.execute("SELECT COUNT(*), COALESCE(AVG(doluluk_yuzde),0) FROM saha_lokasyon").fetchone()
        saha_bazinda = conn.execute("""
            SELECT saha_no, COUNT(*) AS adet, COALESCE(AVG(doluluk_yuzde),0) AS ortalama
            FROM saha_lokasyon GROUP BY saha_no ORDER BY saha_no
        """).fetchall()
        return jsonify({
            "toplam_lokasyon": toplam[0],
            "ortalama_doluluk": round(toplam[1], 1),
            "saha_bazinda": [dict(r) for r in saha_bazinda],
        })
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@saha_bp.route("/api/saha-doluluk/lokasyon/<int:lokasyon_id>/doluluk", methods=["POST"])
def api_saha_doluluk_guncelle(lokasyon_id):
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    d = request.get_json(silent=True) or {}
    try:
        yuzde = float(d.get("doluluk_yuzde", 0))
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "Geçersiz doluluk yüzdesi"}), 400
    yuzde = max(0, min(100, yuzde))
    notu = (d.get("doluluk_notu") or "").strip()

    conn = get_db()
    try:
        var_mi = conn.execute("SELECT id FROM saha_lokasyon WHERE id = ?", (lokasyon_id,)).fetchone()
        if not var_mi:
            return jsonify({"durum": "hata", "mesaj": "Lokasyon bulunamadı"}), 404
        conn.execute("""
            UPDATE saha_lokasyon
            SET doluluk_yuzde = ?, doluluk_notu = ?, guncelleyen = ?,
                guncelleme_tarihi = datetime('now','localtime')
            WHERE id = ?
        """, (yuzde, notu, session.get("kullanici", ""), lokasyon_id))
        conn.commit()
        return jsonify({"durum": "tamam"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@saha_bp.route("/api/saha-doluluk/lokasyon/<int:lokasyon_id>/poligon", methods=["POST"])
def api_saha_doluluk_poligon_guncelle(lokasyon_id):
    # Sadece admin — "Alanları Düzenle" modunda köşe noktalarını kaydeder.
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("rol") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403

    d = request.get_json(silent=True) or {}
    poligon = d.get("poligon")
    if not isinstance(poligon, list) or len(poligon) < 3:
        return jsonify({"durum": "hata", "mesaj": "Geçersiz poligon (en az 3 nokta gerekli)"}), 400
    try:
        temiz = [[float(p[0]), float(p[1])] for p in poligon]
    except (TypeError, ValueError, IndexError):
        return jsonify({"durum": "hata", "mesaj": "Geçersiz poligon formatı"}), 400

    conn = get_db()
    try:
        var_mi = conn.execute("SELECT id FROM saha_lokasyon WHERE id = ?", (lokasyon_id,)).fetchone()
        if not var_mi:
            return jsonify({"durum": "hata", "mesaj": "Lokasyon bulunamadı"}), 404
        conn.execute("UPDATE saha_lokasyon SET poligon = ? WHERE id = ?", (json.dumps(temiz), lokasyon_id))
        conn.commit()
        return jsonify({"durum": "tamam"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@saha_bp.route("/api/saha-doluluk/lokasyon/yeni", methods=["POST"])
def api_saha_doluluk_lokasyon_ekle():
    # Sadece admin — YSL kodlu ana lokasyonların dışında, hurda sahası, timber
    # stock area, çöplük gibi küçük/özel alanları manuel eklemek için.
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("rol") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403

    d = request.get_json(silent=True) or {}
    kod = (d.get("kod") or "").strip()
    ad = (d.get("ad") or "").strip()
    try:
        saha_no = int(d.get("saha_no"))
    except (TypeError, ValueError):
        return jsonify({"durum": "hata", "mesaj": "Geçerli bir saha no girin"}), 400

    if not kod:
        return jsonify({"durum": "hata", "mesaj": "Kod boş olamaz"}), 400
    if saha_no not in (1, 2, 3, 4):
        return jsonify({"durum": "hata", "mesaj": "Saha no 1-4 arasında olmalı"}), 400

    # Yeni alan haritanın ortasına yakın küçük bir kare olarak başlar —
    # admin "Alanları Düzenle" modunda köşelerini gerçek konumuna sürükler.
    varsayilan_poligon = [[0.46, 0.46], [0.52, 0.46], [0.52, 0.52], [0.46, 0.52]]

    conn = get_db()
    try:
        var_mi = conn.execute("SELECT id FROM saha_lokasyon WHERE kod = ?", (kod,)).fetchone()
        if var_mi:
            return jsonify({"durum": "hata", "mesaj": "Bu kod zaten kullanılıyor"}), 409
        conn.execute(
            "INSERT INTO saha_lokasyon (kod, saha_no, ad, poligon) VALUES (?, ?, ?, ?)",
            (kod, saha_no, ad, json.dumps(varsayilan_poligon))
        )
        conn.commit()
        yeni = conn.execute("SELECT id FROM saha_lokasyon WHERE kod = ?", (kod,)).fetchone()
        return jsonify({"durum": "tamam", "id": yeni["id"]})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


@saha_bp.route("/api/saha-doluluk/lokasyon/<int:lokasyon_id>", methods=["DELETE"])
def api_saha_doluluk_lokasyon_sil(lokasyon_id):
    # Sadece admin — manuel eklenen (ya da yanlışlıkla eklenen) bir alanı kaldırmak için.
    if not session.get("kullanici"):
        return jsonify({"durum": "hata", "mesaj": "Giriş gerekli"}), 401
    if session.get("rol") != "admin":
        return jsonify({"durum": "hata", "mesaj": "Bu işlem için yetkiniz yok"}), 403

    conn = get_db()
    try:
        var_mi = conn.execute("SELECT id FROM saha_lokasyon WHERE id = ?", (lokasyon_id,)).fetchone()
        if not var_mi:
            return jsonify({"durum": "hata", "mesaj": "Lokasyon bulunamadı"}), 404
        conn.execute("DELETE FROM saha_lokasyon WHERE id = ?", (lokasyon_id,))
        conn.commit()
        return jsonify({"durum": "tamam"})
    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500
    finally:
        conn.close()


