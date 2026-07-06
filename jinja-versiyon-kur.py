"""
JINJA VERSIYON DÖNÜŞTÜRÜCÜ
==========================

Tüm HTML dosyalarındaki hardcoded versiyon ifadelerini Jinja değişkenine çevirir:

    V4.0          →  V{{ APP_VERSION }}
    Version 4.0   →  Version {{ APP_VERSION }}
    v4.0          →  v{{ APP_VERSION }}
    version 4.0   →  version {{ APP_VERSION }}

KULLANIM:
    py jinja-versiyon-kur.py 4.0 templates/*.html
                            ^ mevcut hardcoded değer

Her dosya için .yedek uzantılı yedek alınır.

ÖNKOŞUL: app.py'ye context processor eklediğinden EMİN OL:

    from version import APP_VERSION

    @app.context_processor
    def inject_app_version():
        return {"APP_VERSION": APP_VERSION}
"""

import re
import sys
import shutil
from pathlib import Path
from glob import glob


def donustur(text, eski):
    """V/v/Version/version + eski versiyon → {{ APP_VERSION }} kalıbı."""
    e = re.escape(eski)

    patterns = [
        # "V4.0"        → "V{{ APP_VERSION }}"
        (r'(?<![a-zA-Z0-9])V\s*' + e + r'(?!\d)',       'V{{ APP_VERSION }}'),
        # "v4.0"        → "v{{ APP_VERSION }}"
        (r'(?<![a-zA-Z0-9])v\s*' + e + r'(?!\d)',       'v{{ APP_VERSION }}'),
        # "Version 4.0" → "Version {{ APP_VERSION }}"
        (r'\bVersion\s+' + e + r'(?!\d)',               'Version {{ APP_VERSION }}'),
        # "version 4.0" → "version {{ APP_VERSION }}"
        (r'\bversion\s+' + e + r'(?!\d)',               'version {{ APP_VERSION }}'),
    ]

    toplam = 0
    for pattern, replacement in patterns:
        text, n = re.subn(pattern, replacement, text)
        toplam += n
    return text, toplam


def dosya_donustur(yol, eski):
    if not yol.exists():
        return {"durum": "hata", "mesaj": f"Yok: {yol}"}
    try:
        orig = yol.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return {"durum": "hata", "mesaj": "UTF-8 okunamadi"}

    yeni, sayi = donustur(orig, eski)
    if sayi == 0:
        return {"durum": "yok"}

    yedek = yol.with_suffix(yol.suffix + '.yedek')
    shutil.copy2(yol, yedek)
    yol.write_text(yeni, encoding='utf-8')

    return {"durum": "ok", "yedek": str(yedek), "sayi": sayi}


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    eski = sys.argv[1]
    dosya_arg = sys.argv[2:]

    if not re.match(r'^\d+(\.\d+)*$', eski):
        print(f"HATA: Gecersiz versiyon formati: '{eski}' (ornek: 4.0)")
        sys.exit(1)

    print(f"Jinja Sablonuna Cevir: {eski} -> {{{{ APP_VERSION }}}}")
    print("=" * 62)
    print("UYARI: app.py'ye context_processor eklediginden emin ol!")
    print()

    # Dosyalari topla
    tum = []
    for arg in dosya_arg:
        if '*' in arg or '?' in arg:
            tum.extend(glob(arg))
        else:
            tum.append(arg)

    if not tum:
        print("Eslesen dosya yok")
        sys.exit(1)

    ok = 0
    yok = 0
    hata = 0
    toplam = 0

    for d in sorted(set(tum)):
        yol = Path(d)
        sonuc = dosya_donustur(yol, eski)

        if sonuc["durum"] == "ok":
            print(f"  OK   {yol.name:40} {sonuc['sayi']} degisiklik")
            ok += 1
            toplam += sonuc['sayi']
        elif sonuc["durum"] == "yok":
            print(f"  --   {yol.name:40} versiyon yok")
            yok += 1
        else:
            print(f"  HATA {yol.name:40} {sonuc['mesaj']}")
            hata += 1

    print()
    print("=" * 62)
    print(f"BITTI: {ok} dosya guncellendi, {toplam} degisiklik")
    if yok:  print(f"       {yok} dosyada versiyon yoktu")
    if hata: print(f"       {hata} dosyada hata")
    print()
    print("SONRAKI ADIMLAR:")
    print("  1. Flask'i restart et")
    print("  2. Sayfalari kontrol et - versiyon dogru gozukmeli")
    print("  3. Yeni surumde: version.py'de degistir + restart -> her sayfa guncel")
    print()
    print("Bir sey ters giderse .yedek dosyalarindan geri getir")


if __name__ == "__main__":
    main()