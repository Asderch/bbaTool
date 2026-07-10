# -*- coding: utf-8 -*-
"""
versiyon_db.py — BBA Ambar Data Sürüm Yönetimi

Ortak klasördeki versiyon.json'dan minimum kabul edilen sürümü okur.
Uygulama açılışta versiyonunu kontrol eder — eskiyse tüm sayfalar kilit,
sadece /guncelle sayfası erişilebilir olur.
"""

import os
import sys
import json
import shutil
from datetime import datetime
from flask import Blueprint, request, jsonify, session

versiyon_bp = Blueprint("versiyon", __name__)

# ─── Ortak klasör (kullanici_db ile aynı) ───
ORTAK_KLASOR = r"K:\Warehouse\Yeşilovacık\12_Paylaşım Klasörü\01-BBA\bba-tool"


def _versiyon_klasor_bul():
    """Ortak klasör varsa onu, yoksa exe/script klasörünü döner."""
    if os.path.isdir(ORTAK_KLASOR):
        return ORTAK_KLASOR
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


VERSIYON_KLASOR = _versiyon_klasor_bul()
VERSIYON_JSON = os.path.join(VERSIYON_KLASOR, "versiyon.json")
YEDEK_KLASOR = os.path.join(VERSIYON_KLASOR, "versiyon_yedek")


# ─── Sabit: Uygulamanın kendi versiyonu ───
# Bu değer PyInstaller build sırasında sabittir, exe'ye gömülür.
# Değiştirmek için app.py'de APP_VERSION'u güncelle + yeniden build al.
APP_VERSION = "4.2"


# ─── İlk kurulum verileri ───
ILK_VERSIYON_VERISI = {
    "min_versiyon": "4.0",
    "son_guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "guncelleyen": "sistem",
    "mesaj": "İlk kurulum",
}


def versiyon_dosyasini_hazirla():
    """Uygulama açılışında çağrılır. Yoksa oluşturur."""
    if not os.path.exists(VERSIYON_JSON):
        try:
            with open(VERSIYON_JSON, "w", encoding="utf-8") as f:
                json.dump(ILK_VERSIYON_VERISI, f, ensure_ascii=False, indent=2)
            print(f"[Versiyon DB] Ilk defa olusturuldu: {VERSIYON_JSON}")
        except Exception as e:
            print(f"[Versiyon DB] Olusturma HATA: {e}")


def versiyon_oku():
    """versiyon.json'u oku, boş/yoksa varsayılan döner."""
    if not os.path.exists(VERSIYON_JSON):
        versiyon_dosyasini_hazirla()
    try:
        with open(VERSIYON_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Versiyon DB] Okuma hatasi: {e}")
        return ILK_VERSIYON_VERISI.copy()


def versiyon_yaz(veri):
    """Versiyon bilgisini yaz. Önce yedek alır."""
    try:
        if os.path.exists(VERSIYON_JSON):
            os.makedirs(YEDEK_KLASOR, exist_ok=True)
            yedek_ad = f"versiyon_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            try:
                shutil.copy2(VERSIYON_JSON, os.path.join(YEDEK_KLASOR, yedek_ad))
            except: pass
            # Son 20 yedek
            try:
                yedekler = sorted(os.listdir(YEDEK_KLASOR), reverse=True)
                for eski in yedekler[20:]:
                    try: os.remove(os.path.join(YEDEK_KLASOR, eski))
                    except: pass
            except: pass

        with open(VERSIYON_JSON, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[Versiyon DB] Yazma hatasi: {e}")
        return False


def _versiyon_parse(v):
    """
    '4.0' → (4, 0)
    '4.2.1' → (4, 2, 1)
    Karşılaştırılabilir tuple döner.
    """
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except:
        return (0, 0)


def guncel_mi():
    """
    Bu exe'nin versiyonu min_versiyon'a eşit veya üstündeyse True.
    Aksi halde False = KİLİT.
    Ortak klasöre erişim yoksa (K: kopuk) True döner — kullanıcı çalışabilir,
    ağ geri gelince kontrol edilir.
    """
    if not os.path.isdir(ORTAK_KLASOR):
        # K: kopuk, kilitleme (çünkü kontrol edemiyoruz — kullanıcı mağdur olmasın)
        return True

    veri = versiyon_oku()
    min_v = _versiyon_parse(veri.get("min_versiyon", "0.0"))
    mevcut = _versiyon_parse(APP_VERSION)
    return mevcut >= min_v


# ─────────────────────────────────────────────
# API ENDPOINT'LERİ
# ─────────────────────────────────────────────

@versiyon_bp.route("/api/versiyon/durum", methods=["GET"])
def api_versiyon_durum():
    """Versiyon bilgisini + kilit durumunu döner."""
    veri = versiyon_oku()
    ortak_erisim = os.path.isdir(ORTAK_KLASOR)
    return jsonify({
        "app_version":      APP_VERSION,
        "min_versiyon":     veri.get("min_versiyon", "0.0"),
        "son_guncelleme":   veri.get("son_guncelleme", ""),
        "guncelleyen":      veri.get("guncelleyen", ""),
        "mesaj":            veri.get("mesaj", ""),
        "guncel_mi":        guncel_mi(),
        "ortak_erisim":     ortak_erisim,
        "json_yolu":        VERSIYON_JSON,
    })


@versiyon_bp.route("/api/versiyon/guncelle", methods=["POST"])
def api_versiyon_guncelle():
    """Minimum kabul edilen sürümü değiştir. Sadece admin."""
    try:
        # Session kontrolü — sadece "admin" kullanıcı adı
        if session.get("kullanici") != "admin":
            return jsonify({"durum": "hata", "mesaj": "Yetkiniz yok"}), 403

        d = request.get_json() or {}
        yeni_min = str(d.get("min_versiyon", "")).strip()
        mesaj = str(d.get("mesaj", "")).strip()

        if not yeni_min:
            return jsonify({"durum": "hata", "mesaj": "min_versiyon zorunlu"}), 400

        # Basit versiyon formatı kontrolü (rakam.rakam veya rakam.rakam.rakam)
        parts = yeni_min.split(".")
        if not all(p.isdigit() for p in parts) or len(parts) < 2 or len(parts) > 3:
            return jsonify({"durum": "hata", "mesaj": "Format: 4.0 veya 4.2.1"}), 400

        veri = {
            "min_versiyon":   yeni_min,
            "son_guncelleme": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "guncelleyen":    session.get("kullanici", "admin"),
            "mesaj":          mesaj or f"V{yeni_min} zorunlu hale getirildi",
        }

        if versiyon_yaz(veri):
            return jsonify({
                "durum": "ok",
                "mesaj": f"Minimum sürüm V{yeni_min} olarak ayarlandı",
                "veri": veri,
            })
        return jsonify({"durum": "hata", "mesaj": "Dosya yazılamadı"}), 500

    except Exception as e:
        return jsonify({"durum": "hata", "mesaj": str(e)}), 500