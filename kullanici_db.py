# -*- coding: utf-8 -*-
"""
kullanici_db.py — BBA Ambar Data Kullanıcı ve Rol Yönetimi

JSON tabanlı, ortak klasörde tutulur (K:\\Warehouse\\...\\01-BBA).
Tüm PC'lerden aynı JSON dosyası okunur — bir yerde eklenen kullanıcı
anında tüm şantiye PC'lerinde görünür.

Fallback: Ortak klasöre erişim yoksa exe/script klasörü kullanılır.
"""

import os
import sys
import json
import shutil
from datetime import datetime
from flask import Blueprint, request, jsonify

kullanici_bp = Blueprint("kullanici", __name__)

# ─────────────────────────────────────────────
# ORTAK KLASÖR — Sunucudaki paylaşım
# ─────────────────────────────────────────────
ORTAK_KLASOR = r"K:\Warehouse\Yeşilovacık\12_Paylaşım Klasörü\01-BBA\bba-tool"


def _kullanici_dosya_yolu_bul():
    """
    Ortak klasör erişilebilirse onu döner.
    Değilse (K: bağlı değil, ağ kopuk) exe/script klasörünü döner.
    Konsola hangisinin kullanıldığını yazar.
    """
    if os.path.isdir(ORTAK_KLASOR):
        print(f"[Kullanici DB] Ortak klasor kullaniliyor: {ORTAK_KLASOR}")
        return ORTAK_KLASOR

    # Fallback: exe veya script klasörü
    if getattr(sys, 'frozen', False):
        fallback = os.path.dirname(sys.executable)
    else:
        fallback = os.path.dirname(os.path.abspath(__file__))
    print(f"[Kullanici DB] UYARI: Ortak klasore erisim yok!")
    print(f"[Kullanici DB] Lokal kullaniliyor: {fallback}")
    print(f"[Kullanici DB] DIKKAT: Bu PC'deki degisiklikler digerlerine yansimaz!")
    return fallback


KULLANICI_KLASOR = _kullanici_dosya_yolu_bul()
KULLANICI_JSON = os.path.join(KULLANICI_KLASOR, "kullanicilar.json")
YEDEK_KLASOR = os.path.join(KULLANICI_KLASOR, "kullanicilar_yedek")


# ─────────────────────────────────────────────
# VARSAYILAN ROLLER (kod içinde sabit)
# ─────────────────────────────────────────────
VARSAYILAN_ROLLER = {
    "admin": {
        "panel_gor":         True,
        "plan_olustur":      True,
        "plan_gor":          True,
        "plan_sil":          True,
        "plan_kapat":        True,
        "kalem_ekle":        True,
        "kalem_toplu_ekle":  True,
        "kalem_guncelle":    True,
        "kalem_sil":         True,
        "kalem_gonder":      True,
        "kalem_sifirla":     True,
        "toplu_devret":      True,
        "export_pdf":        True,
        "export_excel":      True,
        "import_excel":      True,
        "rapor_gor":         True,
        "dosya_sil":         True,
        "personel_izin":     True,
        "kullanici_yonet":   True,
    },
    "hazirlayan": {
        "panel_gor":         False,
        "plan_olustur":      True,
        "plan_gor":          True,
        "plan_sil":          True,
        "plan_kapat":        True,
        "kalem_ekle":        True,
        "kalem_toplu_ekle":  True,
        "kalem_guncelle":    True,
        "kalem_sil":         True,
        "kalem_gonder":      True,
        "kalem_sifirla":     True,
        "toplu_devret":      True,
        "export_pdf":        False,
        "export_excel":      False,
        "import_excel":      False,
        "rapor_gor":         True,
        "dosya_sil":         False,
        "personel_izin":     False,
        "kullanici_yonet":   False,
    },
    "goruntuleyici": {
        "panel_gor":         True,
        "plan_olustur":      False,
        "plan_gor":          True,
        "plan_sil":          False,
        "plan_kapat":        False,
        "kalem_ekle":        False,
        "kalem_toplu_ekle":  False,
        "kalem_guncelle":    False,
        "kalem_sil":         False,
        "kalem_gonder":      False,
        "kalem_sifirla":     False,
        "toplu_devret":      False,
        "export_pdf":        True,
        "export_excel":      True,
        "import_excel":      False,
        "rapor_gor":         True,
        "dosya_sil":         False,
        "personel_izin":     False,
        "kullanici_yonet":   False,
    },
    "sayim": {
        "panel_gor":         False,
        "plan_olustur":      False,
        "plan_gor":          False,
        "plan_sil":          False,
        "plan_kapat":        False,
        "kalem_ekle":        False,
        "kalem_toplu_ekle":  False,
        "kalem_guncelle":    False,
        "kalem_sil":         False,
        "kalem_gonder":      False,
        "kalem_sifirla":     False,
        "toplu_devret":      False,
        "export_pdf":        False,
        "export_excel":      False,
        "import_excel":      False,
        "rapor_gor":         False,
        "dosya_sil":         False,
        "personel_izin":     False,
        "kullanici_yonet":   False,
    },

    "yasakli": {
        "panel_gor":         False, "plan_olustur":    False, "plan_gor":        False,
        "plan_sil":          False, "plan_kapat":      False, "kalem_ekle":      False,
        "kalem_toplu_ekle":  False, "kalem_guncelle":  False, "kalem_sil":       False,
        "kalem_gonder":      False, "kalem_sifirla":   False, "toplu_devret":    False,
        "export_pdf":        False, "export_excel":    False, "import_excel":    False,
        "rapor_gor":         False, "dosya_sil":       False, "personel_izin":   False,
        "kullanici_yonet":   False,
    },
}


# ─────────────────────────────────────────────
# İLK KURULUM KULLANICILARI
# JSON yoksa bu liste ile ilk defa oluşturulur
# ─────────────────────────────────────────────
ILK_KULLANICILAR = {
    "admin":   {"sifre": "163131", "ad": "Admin",              "rol": "admin"},
    "bakar":   {"sifre": "bb5528", "ad": "Berkcan Burak Akar", "rol": "admin"},
    "okaraca": {"sifre": "ok1234", "ad": "Özge Karaca",        "rol": "admin"},
    "bkonyar": {"sifre": "bk1234", "ad": "Bora Konyar",        "rol": "admin"},
    "pgur":    {"sifre": "pg1453", "ad": "Pınar Ecem Gür",     "rol": "admin"},
    "hors":    {"sifre": "ho1793", "ad": "Havva Örs",          "rol": "admin"},
    "sayim":   {"sifre": "sy1234", "ad": "Sayım Ekibi",        "rol": "sayim"},
}


# ─────────────────────────────────────────────
# DOSYA İŞLEMLERİ
# ─────────────────────────────────────────────

def kullanici_dosyasini_hazirla():
    """
    Uygulama açılışında çağrılır.
    Eğer kullanicilar.json yoksa, ILK_KULLANICILAR ile oluşturur.
    """
    if not os.path.exists(KULLANICI_JSON):
        veri = {
            "kullanicilar": ILK_KULLANICILAR,
            "olusturulma": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            with open(KULLANICI_JSON, "w", encoding="utf-8") as f:
                json.dump(veri, f, ensure_ascii=False, indent=2)
            print(f"[Kullanici DB] Ilk defa olusturuldu: {KULLANICI_JSON}")
        except Exception as e:
            print(f"[Kullanici DB] Ilk olusturma HATA: {e}")


def kullanicilari_oku():
    """JSON'dan kullanıcıları oku. Hata olursa boş dict döner."""
    if not os.path.exists(KULLANICI_JSON):
        kullanici_dosyasini_hazirla()

    try:
        with open(KULLANICI_JSON, "r", encoding="utf-8") as f:
            veri = json.load(f)
        return veri.get("kullanicilar", {})
    except Exception as e:
        print(f"[Kullanici DB] Okuma hatasi: {e}")
        return {}


def kullanicilari_yaz(kullanicilar):
    """
    Kullanıcıları JSON'a yaz. Her yazma öncesi yedek alır.
    Son 20 yedeği tutar, eskileri siler.
    """
    try:
        # Önce yedek al
        if os.path.exists(KULLANICI_JSON):
            os.makedirs(YEDEK_KLASOR, exist_ok=True)
            yedek_ad = f"kullanicilar_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            try:
                shutil.copy2(KULLANICI_JSON, os.path.join(YEDEK_KLASOR, yedek_ad))
            except Exception as e:
                print(f"[Kullanici DB] Yedek alinamadi: {e}")

            # Son 20 yedeği tut
            try:
                yedekler = sorted(os.listdir(YEDEK_KLASOR), reverse=True)
                for eski in yedekler[20:]:
                    try:
                        os.remove(os.path.join(YEDEK_KLASOR, eski))
                    except:
                        pass
            except:
                pass

        veri = {
            "kullanicilar": kullanicilar,
            "guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(KULLANICI_JSON, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[Kullanici DB] Yazma hatasi: {e}")
        return False


def kullanici_bilgi(username):
    """
    Bir kullanıcının bilgi + yetkilerini döner.
    Şifre DÖNMEZ. Yoksa None döner.
    """
    kullanicilar = kullanicilari_oku()
    k = kullanicilar.get(username)
    if not k:
        return None
    rol = k.get("rol", "goruntuleyici")
    return {
        "ad":       k.get("ad", ""),
        "rol":      rol,
        "yetkiler": VARSAYILAN_ROLLER.get(rol, {}),
    }


def sifre_dogrula(username, sifre):
    """Login için — kullanıcı adı + şifre kontrolü. True/False döner."""
    kullanicilar = kullanicilari_oku()
    k = kullanicilar.get(username)
    if not k:
        return False
    return k.get("sifre") == sifre


# ─────────────────────────────────────────────
# API ENDPOINT'LERİ
# ─────────────────────────────────────────────

@kullanici_bp.route("/api/kullanici/liste", methods=["GET"])
def api_kullanici_liste():
    """Tüm kullanıcıları listele. Şifreler GİZLİ."""
    kullanicilar = kullanicilari_oku()
    liste = []
    for username, bilgi in kullanicilar.items():
        liste.append({
            "username": username,
            "ad":       bilgi.get("ad", ""),
            "rol":      bilgi.get("rol", ""),
            # sifre burada YOK — güvenlik
        })
    # Kullanıcı adına göre sırala (admin en üstte olsun)
    liste.sort(key=lambda x: (x["rol"] != "admin", x["ad"].lower()))
    return jsonify(liste)


@kullanici_bp.route("/api/kullanici/roller", methods=["GET"])
def api_kullanici_roller():
    """Mevcut rolleri ve yetki matriksini döner (admin sayfası için)."""
    return jsonify(VARSAYILAN_ROLLER)


@kullanici_bp.route("/api/kullanici/ekle", methods=["POST"])
def api_kullanici_ekle():
    """Yeni kullanıcı ekle."""
    try:
        d = request.get_json() or {}
        username = (d.get("username") or "").strip().lower()
        sifre    = (d.get("sifre") or "").strip()
        ad       = (d.get("ad") or "").strip()
        rol      = (d.get("rol") or "").strip()

        # Validasyonlar
        if not username or not sifre or not ad or not rol:
            return jsonify({"durum": "hata", "mesaj": "Tüm alanlar zorunlu"}), 400
        if rol not in VARSAYILAN_ROLLER:
            return jsonify({"durum": "hata", "mesaj": f"Geçersiz rol: {rol}"}), 400
        if not username.replace("_", "").isalnum():
            return jsonify({"durum": "hata", "mesaj": "Kullanıcı adı sadece harf/rakam/alt çizgi içerebilir"}), 400
        if len(username) < 2 or len(username) > 20:
            return jsonify({"durum": "hata", "mesaj": "Kullanıcı adı 2-20 karakter olmalı"}), 400
        if len(sifre) < 4:
            return jsonify({"durum": "hata", "mesaj": "Şifre en az 4 karakter olmalı"}), 400

        kullanicilar = kullanicilari_oku()
        if username in kullanicilar:
            return jsonify({"durum": "hata", "mesaj": "Bu kullanıcı adı zaten var"}), 400

        kullanicilar[username] = {"sifre": sifre, "ad": ad, "rol": rol}

        if kullanicilari_yaz(kullanicilar):
            return jsonify({"durum": "ok", "mesaj": f"{ad} eklendi"})
        return jsonify({"durum": "hata", "mesaj": "Dosya yazılamadı"}), 500

    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@kullanici_bp.route("/api/kullanici/guncelle/<username>", methods=["POST"])
def api_kullanici_guncelle(username):
    """Mevcut kullanıcıyı güncelle. Sadece gönderilen alanlar değişir."""
    try:
        d = request.get_json() or {}
        kullanicilar = kullanicilari_oku()

        if username not in kullanicilar:
            return jsonify({"durum": "hata", "mesaj": "Kullanıcı bulunamadı"}), 404

        k = kullanicilar[username]

        # Ad
        if "ad" in d and d["ad"]:
            k["ad"] = str(d["ad"]).strip()

        # Rol
        if "rol" in d and d["rol"]:
            if d["rol"] not in VARSAYILAN_ROLLER:
                return jsonify({"durum": "hata", "mesaj": "Geçersiz rol"}), 400

            # Son admin'in rolü değiştirilmesin
            adminler = [u for u, v in kullanicilar.items() if v.get("rol") == "admin"]
            if k.get("rol") == "admin" and d["rol"] != "admin" and len(adminler) == 1:
                return jsonify({"durum": "hata", "mesaj": "Son admin'in rolü değiştirilemez"}), 400

            k["rol"] = d["rol"]

        # Şifre (boş gelirse değişmez)
        if "sifre" in d and d["sifre"]:
            sifre = str(d["sifre"]).strip()
            if len(sifre) < 4:
                return jsonify({"durum": "hata", "mesaj": "Şifre en az 4 karakter olmalı"}), 400
            k["sifre"] = sifre

        kullanicilar[username] = k
        if kullanicilari_yaz(kullanicilar):
            return jsonify({"durum": "ok", "mesaj": f"{username} güncellendi"})
        return jsonify({"durum": "hata", "mesaj": "Dosya yazılamadı"}), 500

    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@kullanici_bp.route("/api/kullanici/sil/<username>", methods=["DELETE"])
def api_kullanici_sil(username):
    """Kullanıcıyı sil. Son admin'e izin verilmez."""
    try:
        kullanicilar = kullanicilari_oku()
        if username not in kullanicilar:
            return jsonify({"durum": "hata", "mesaj": "Kullanıcı bulunamadı"}), 404

        # Son admin'i silmeye izin verme
        adminler = [k for k, v in kullanicilar.items() if v.get("rol") == "admin"]
        if kullanicilar[username].get("rol") == "admin" and len(adminler) == 1:
            return jsonify({"durum": "hata", "mesaj": "Son admin silinemez"}), 400

        del kullanicilar[username]
        if kullanicilari_yaz(kullanicilar):
            return jsonify({"durum": "ok", "mesaj": f"{username} silindi"})
        return jsonify({"durum": "hata", "mesaj": "Dosya yazılamadı"}), 500

    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500


@kullanici_bp.route("/api/kullanici/db-durum", methods=["GET"])
def api_kullanici_db_durum():
    """
    DB'nin nerede tutulduğunu döner (admin sayfasında gösterilir).
    Ortak klasör mü lokal mi?
    """
    ortak_erisim = os.path.isdir(ORTAK_KLASOR)
    kullanici_sayisi = len(kullanicilari_oku())
    return jsonify({
        "ortak_klasor":     ORTAK_KLASOR,
        "ortak_erisim":     ortak_erisim,
        "aktif_yol":        KULLANICI_KLASOR,
        "json_yolu":        KULLANICI_JSON,
        "kullanici_sayisi": kullanici_sayisi,
        "yedek_klasoru":    YEDEK_KLASOR,
    })