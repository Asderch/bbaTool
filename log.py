import sqlite3
import os
from flask import request, session
from app import DB_KLASOR, DB_DOSYA

def admin_log(islem, tablo, hedef_id=None, detay=""):
    db = sqlite3.connect(os.path.join(DB_KLASOR, DB_DOSYA))
    cur = db.cursor()

    kullanici = session.get("kullanici", "bilinmiyor")
    ip = request.remote_addr

    cur.execute("""
    INSERT INTO admin_loglar
    (kullanici, islem, hedef_tablo, hedef_id, detay, ip_adres)
    VALUES (?,?,?,?,?,?)
    """, (kullanici, islem, tablo, hedef_id, detay, ip))

    db.commit()
    db.close()