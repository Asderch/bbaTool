"""
import_mm60.py — MM60 Excel dosyasını malzeme_katalog tablosuna yükler

Kullanım:
    python import_mm60.py "C:\\bba-tool\\mm60_export.xlsx"

veya parametre vermezsen interaktif olarak sorar.

Bu script malzeme_kontrol.py'deki aynı normalizasyon ve sütun eşleşmesini
kullanır — yani aynı sonucu üretir (web upload ile).
"""

import sys, os, argparse

# malzeme_kontrol modülünü import et — aynı dizinde olmalı
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pandas as pd
except ImportError:
    print("HATA: pandas kurulu değil.")
    print("Çözüm: pip install pandas openpyxl xlrd")
    sys.exit(1)

try:
    from malzeme_kontrol import (
        init_malzeme_db, get_conn, tokenize,
        _SUTUN_ALIAS, _sutun_eslestir
    )
except ImportError as e:
    print(f"HATA: malzeme_kontrol.py bulunamadı veya hatalı: {e}")
    print("Bu scripti malzeme_kontrol.py ile aynı klasörde çalıştırın.")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="MM60 Excel → SQLite malzeme_katalog import")
    parser.add_argument("dosya", nargs="?", help="Excel dosyası yolu")
    parser.add_argument("--temizle", action="store_true", help="Önce tabloyu boşalt")
    args = parser.parse_args()

    # Dosya yolu sorgula
    dosya = args.dosya
    if not dosya:
        dosya = input("Excel dosyasının tam yolunu yapıştır: ").strip().strip('"').strip("'")

    if not os.path.exists(dosya):
        print(f"HATA: Dosya bulunamadı: {dosya}")
        sys.exit(1)

    print(f"\n=== MM60 Katalog Import ===")
    print(f"Dosya: {dosya}")

    # DB hazırla
    init_malzeme_db()

    # Excel oku
    print("Excel okunuyor...")
    try:
        df = pd.read_excel(dosya, dtype=str)
    except Exception as e:
        print(f"HATA: Excel okunamadı: {e}")
        sys.exit(1)

    df = df.fillna("")
    print(f"Toplam {len(df)} satır okundu, {len(df.columns)} kolon var.")
    print(f"Excel kolonları: {list(df.columns)}")

    # Sütun eşle
    mapping = _sutun_eslestir(df.columns)
    print(f"\nEşlenen sütunlar:")
    for std, exc in mapping.items():
        print(f"  {std:18} <- {exc}")

    if "malzeme_no" not in mapping:
        print("\nHATA: 'Malzeme' veya 'MATNR' kolonu bulunamadı!")
        print("Kontrol et: Excel'de malzeme kodu sütunu var mı?")
        print(f"\nMevcut kolonlar:")
        for c in df.columns:
            print(f"  - {c}")
        sys.exit(1)

    # Temizle?
    conn = get_conn()
    cur = conn.cursor()
    try:
        if args.temizle:
            mevcut = cur.execute("SELECT COUNT(*) FROM malzeme_katalog").fetchone()[0]
            if mevcut > 0:
                onay = input(f"\nTabloda {mevcut} kayıt var. Hepsini silelim mi? (evet/hayır): ").strip().lower()
                if onay in ("evet", "e", "yes", "y"):
                    cur.execute("DELETE FROM malzeme_katalog")
                    print(f"  ✓ {mevcut} kayıt silindi")
                else:
                    print("  → Mevcut kayıtlar korundu, üzerine yazılacak")

        # Kayıtları hazırla
        kayitlar = []
        for idx, satir in df.iterrows():
            malz_no = str(satir.get(mapping["malzeme_no"], "")).strip()
            if not malz_no or malz_no.lower() in ("nan", "none", ""):
                continue

            kisa_tr = str(satir.get(mapping.get("kisa_metin_tr", ""), "")).strip()
            uzun_tr = str(satir.get(mapping.get("uzun_metin_tr", ""), "")).strip()
            tokens_str = " ".join(tokenize(kisa_tr + " " + uzun_tr))

            kayitlar.append((
                malz_no,
                kisa_tr,
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
            print("\nHATA: Geçerli kayıt bulunamadı")
            sys.exit(1)

        print(f"\n{len(kayitlar)} kayıt INSERT/UPDATE ediliyor...")

        # Bulk upsert
        cur.executemany("""
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

        cur.execute(
            "INSERT INTO malzeme_import_log (dosya_adi, satir_sayisi, pc_adi) VALUES (?, ?, ?)",
            (os.path.basename(dosya), len(kayitlar), os.environ.get("COMPUTERNAME", "unknown"))
        )
        conn.commit()

        toplam = cur.execute("SELECT COUNT(*) FROM malzeme_katalog").fetchone()[0]
        print(f"\n✓ TAMAMLANDI")
        print(f"  Bu işlemde aktarılan: {len(kayitlar)}")
        print(f"  Toplam katalog kaydı: {toplam}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()