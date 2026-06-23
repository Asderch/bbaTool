"""
TEMA UYUMLU HALE GETİR — BBA Ambar Data
========================================

Bu script, eski custom CSS değişkenlerini (var(--bg-color), var(--card-bg) vb.)
ve hardcoded hex renkleri (#3ecf8e, #22c55e vb.) style.css'in gerçek
değişkenleriyle (var(--bg), var(--primary), var(--green) vb.) değiştirir.

Sonuç: Sayfa light/dark tema toggle ile otomatik uyumlu hale gelir,
       diğer sayfalarınla aynı renk paletini paylaşır.

KULLANIM:
    py tema_uyumlu_yap.py fark-raporu.html
    py tema_uyumlu_yap.py agirlik-hesaplama.html
    py tema_uyumlu_yap.py *.html

Orijinal dosya .yedek uzantısıyla yedeklenir.
HTML/JS yapısına ve fonksiyonaliteye DOKUNMAZ — sadece CSS renk değerleri.
"""

import re
import sys
import os
import shutil
from pathlib import Path


# ─────────────────────────────────────────────────────────────────
# 1. ESKİ CUSTOM DEĞİŞKENLER → STYLE.CSS GERÇEK DEĞİŞKENLERİ
#    var(--bg-color, #0f172a) → var(--bg)
#    var(--card-bg, #1e293b)  → var(--bg-white)
# ─────────────────────────────────────────────────────────────────
DEGISKEN_DONUSUMLERI = [
    # (regex pattern, replacement)
    (r'var\(\s*--bg-color\s*,\s*#[0-9a-fA-F]{3,8}\s*\)',     'var(--bg)'),
    (r'var\(\s*--bg-color\s*\)',                             'var(--bg)'),

    (r'var\(\s*--card-bg\s*,\s*#[0-9a-fA-F]{3,8}\s*\)',      'var(--bg-white)'),
    (r'var\(\s*--card-bg\s*\)',                              'var(--bg-white)'),

    (r'var\(\s*--border-color\s*,\s*#[0-9a-fA-F]{3,8}\s*\)', 'var(--border)'),
    (r'var\(\s*--border-color\s*\)',                         'var(--border)'),

    (r'var\(\s*--text-color\s*,\s*#[0-9a-fA-F]{3,8}\s*\)',   'var(--text)'),
    (r'var\(\s*--text-color\s*\)',                           'var(--text)'),

    (r'var\(\s*--muted\s*,\s*#[0-9a-fA-F]{3,8}\s*\)',        'var(--text3)'),
    (r'var\(\s*--muted\s*\)',                                'var(--text3)'),

    (r'var\(\s*--accent-color\s*,\s*#[0-9a-fA-F]{3,8}\s*\)', 'var(--primary)'),
    (r'var\(\s*--accent-color\s*\)',                         'var(--primary)'),

    (r'var\(\s*--hover-bg\s*,\s*#[0-9a-fA-F]{3,8}\s*\)',     'var(--bg-hover)'),
    (r'var\(\s*--hover-bg\s*\)',                             'var(--bg-hover)'),
]


# ─────────────────────────────────────────────────────────────────
# 2. HARDCODED HEX RENKLER → CSS DEĞİŞKENLERİ
#    Style.css paletindeki tam karşılıkları.
#    DİKKAT: Sadece <style> bloğu içinde uygulanır (JS string'lerini etkilemez)
# ─────────────────────────────────────────────────────────────────
HEX_RENK_DONUSUMLERI = {
    # ── Yeşil tonları → --primary / --green ──
    '#3ecf8e': 'var(--primary)',     # Style.css primary
    '#3dbc8e': 'var(--primary)',     # Eski paletten - en yakın
    '#4ee0a0': 'var(--primary-light)',
    '#22c55e': 'var(--green)',       # Yeşil (style.css'te green ve primary aynı renkte ama semantik ayrı)
    '#16a34a': 'var(--green)',
    '#15803d': 'var(--green)',

    # ── Mavi tonları → --blue ──
    '#3b82f6': 'var(--blue)',
    '#6366f1': 'var(--blue)',
    '#4f46e5': 'var(--blue)',
    '#818cf8': 'var(--blue)',
    '#2563eb': 'var(--blue)',

    # ── Sarı/amber tonları → --amber ──
    '#f59e0b': 'var(--amber)',
    '#eab308': 'var(--amber)',
    '#ca8a04': 'var(--amber)',
    '#fbbf24': 'var(--amber)',

    # ── Kırmızı tonları → --red ──
    '#dc2626': 'var(--red)',
    '#ef4444': 'var(--red)',
    '#b91c1c': 'var(--red)',

    # ── Mor tonları → --purple ──
    '#a855f7': 'var(--purple)',
    '#a78bfa': 'var(--purple)',
    '#9333ea': 'var(--purple)',
    '#7c3aed': 'var(--purple)',
    '#8b5cf6': 'var(--purple)',

    # ── Turkuaz tonları → --teal ──
    '#14b8a6': 'var(--teal)',
    '#2dd4bf': 'var(--teal)',
    '#0d9488': 'var(--teal)',

    # ── Pembe → --pink ──
    '#f472b6': 'var(--pink)',
    '#db2777': 'var(--pink)',
    '#ec4899': 'var(--pink)',

    # ── Slate tonları (background/text/border) → style.css değişkenleri ──
    '#0f172a': 'var(--bg)',          # Bg
    '#111111': 'var(--bg)',
    '#1e293b': 'var(--bg-white)',    # Kart bg
    '#1a1e27': 'var(--bg-white)',
    '#1f1f1f': 'var(--bg-white)',
    '#262626': 'var(--bg-hover)',
    '#334155': 'var(--border)',      # Border
    '#475569': 'var(--border)',
    '#2a2a2a': 'var(--border)',

    # ── Text tonları ──
    '#e2e8f0': 'var(--text)',        # Primary text
    '#ededed': 'var(--text)',
    '#cbd5e1': 'var(--text)',
    '#a0a0a0': 'var(--text2)',       # Secondary text
    '#94a3b8': 'var(--text2)',
    '#64748b': 'var(--text3)',       # Muted text
    '#666666': 'var(--text3)',
    '#777777': 'var(--text3)',
}


# ─────────────────────────────────────────────────────────────────
# 3. RGBA RENKLER — Bilgi amaçlı, değiştirilmez
#    (CSS, rgba'yı CSS değişkeninden alamadığı için)
#    rgba(62,207,142,0.1) gibi kullanımlar style.css'te de var,
#    aynı paleti kullandıkları sürece sorun olmaz.
# ─────────────────────────────────────────────────────────────────


def hex_to_var(text: str, sadece_style_bloklarinda: bool = True) -> str:
    """
    Hex renkleri CSS değişkenlerine çevir.
    sadece_style_bloklarinda=True ise, sadece <style>...</style> arasında uygular
    (JS içindeki renk string'leri korunur).
    """
    if not sadece_style_bloklarinda:
        for hex_renk, css_var in HEX_RENK_DONUSUMLERI.items():
            text = re.sub(
                re.escape(hex_renk),
                css_var,
                text,
                flags=re.IGNORECASE
            )
        return text

    # Sadece <style> bloklarını değiştir
    def style_donustur(match):
        ic = match.group(0)
        for hex_renk, css_var in HEX_RENK_DONUSUMLERI.items():
            ic = re.sub(
                re.escape(hex_renk),
                css_var,
                ic,
                flags=re.IGNORECASE
            )
        return ic

    return re.sub(
        r'<style[^>]*>.*?</style>',
        style_donustur,
        text,
        flags=re.DOTALL | re.IGNORECASE
    )


def degisken_donustur(text: str) -> str:
    """Eski custom CSS değişkenlerini doğrularıyla değiştir (her yerde)."""
    for pattern, replacement in DEGISKEN_DONUSUMLERI:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def dosya_donustur(yol: Path) -> dict:
    """Bir dosyayı dönüştür, istatistik döndür."""
    if not yol.exists():
        return {"durum": "hata", "mesaj": f"Dosya bulunamadı: {yol}"}

    orig_text = yol.read_text(encoding='utf-8')

    # 1. Yedek al
    yedek = yol.with_suffix(yol.suffix + '.yedek')
    shutil.copy2(yol, yedek)

    # 2. Değişkenleri dönüştür (her yerde)
    yeni = degisken_donustur(orig_text)
    degisken_say = sum(
        len(re.findall(p, orig_text, flags=re.IGNORECASE))
        for p, _ in DEGISKEN_DONUSUMLERI
    )

    # 3. Hex renkleri dönüştür (sadece <style> içinde — JS'i bozmamak için)
    eski_hex_sayisi = sum(
        len(re.findall(re.escape(h), yeni[yeni.find('<style'):yeni.rfind('</style>') + 8] if '<style' in yeni else '', re.IGNORECASE))
        for h in HEX_RENK_DONUSUMLERI
    )
    yeni = hex_to_var(yeni, sadece_style_bloklarinda=True)

    # 4. Yaz
    yol.write_text(yeni, encoding='utf-8')

    return {
        "durum": "ok",
        "dosya": str(yol),
        "yedek": str(yedek),
        "eski_boy": len(orig_text),
        "yeni_boy": len(yeni),
        "degisken_donusumu": degisken_say,
        "hex_donusumu_style_icinde": eski_hex_sayisi,
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    print("🎨 BBA Tema Uyumlu Hale Getir")
    print("=" * 60)

    toplam_dosya = 0
    toplam_degisken = 0
    toplam_hex = 0

    for arg in sys.argv[1:]:
        # Glob desteği için
        if '*' in arg or '?' in arg:
            from glob import glob
            dosyalar = glob(arg)
        else:
            dosyalar = [arg]

        for d in dosyalar:
            yol = Path(d)
            print(f"\n📄 {yol.name}")
            sonuc = dosya_donustur(yol)
            if sonuc["durum"] == "hata":
                print(f"   ❌ {sonuc['mesaj']}")
                continue

            print(f"   ✅ Tamam!")
            print(f"      Yedek:               {sonuc['yedek']}")
            print(f"      Eski boyut:          {sonuc['eski_boy']:,} bayt")
            print(f"      Yeni boyut:          {sonuc['yeni_boy']:,} bayt")
            print(f"      Değişken dönüşümü:   {sonuc['degisken_donusumu']} adet")
            print(f"      Hex dönüşümü:        {sonuc['hex_donusumu_style_icinde']} adet (sadece <style> içinde)")

            toplam_dosya += 1
            toplam_degisken += sonuc['degisken_donusumu']
            toplam_hex += sonuc['hex_donusumu_style_icinde']

    print()
    print("=" * 60)
    print(f"🎉 {toplam_dosya} dosya dönüştürüldü.")
    print(f"   Toplam {toplam_degisken} değişken + {toplam_hex} hex renk → CSS değişkenine çevrildi.")
    print()
    print("📝 Sonraki adımlar:")
    print("   1. Tarayıcıda Ctrl+F5 ile yenile")
    print("   2. Açık/Koyu tema toggle ile test et")
    print("   3. Eğer bir şey ters görünürse, .yedek dosyasını geri kopyala")


if __name__ == "__main__":
    main()