/* ---- BBA TEMA SİSTEMİ ---- */
/* İki bağımsız eksen var:
   1) data-theme  = "light" / "dark"   → sağ üstteki ay/güneş ikonu bunu değiştirir (mod)
   2) data-tema-ailesi = "mint" / ...  → sol alttaki sidebar butonu bunu değiştirir (renk ailesi/tema)
   Her tema ailesinin kendi light+dark ikilisi olur. Yeni bir tema eklemek için
   TEMA_AILELERI'ne bir satır ekleyip style.css'te o id için [data-tema-ailesi="..."] blokları yazmak yeterli. */

(function(){
  // Sayfa yüklenmeden önce uygula (flash önleme)
  const kayitliTema = localStorage.getItem('bba-tema') || 'light';
  const kayitliAile = localStorage.getItem('bba-tema-ailesi') || 'mint';
  document.documentElement.setAttribute('data-theme', kayitliTema);
  document.documentElement.setAttribute('data-tema-ailesi', kayitliAile);
})();

// Kayıtlı tema aileleri — yeni bir tema eklemek için buraya bir satır eklemek yeterli.
const TEMA_AILELERI = [
  { id: 'mint',   ad: 'Mint',   ikon: 'fa-leaf' },
  { id: 'amber',  ad: 'Amber',  ikon: 'fa-fire' },
  { id: 'indigo', ad: 'Indigo', ikon: 'fa-water' },
  { id: 'orkide', ad: 'Orkide', ikon: 'fa-gem'  },
];

/**
 * Sağ üstteki ay/güneş ikonu — sadece açık/koyu MODU değiştirir, tema ailesine dokunmaz.
 */
function temaToggle() {
  const html = document.documentElement;
  const mevcutTema = html.getAttribute('data-theme') || 'light';
  const yeniTema = mevcutTema === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', yeniTema);
  localStorage.setItem('bba-tema', yeniTema);
  temaButonlariGuncelle(yeniTema);
}

/**
 * Sol alttaki sidebar butonu — tema AİLESİ seçim menüsünü aç/kapat.
 */
function temaSeciciAcKapat(tetikleyiciEl) {
  const mevcutPanel = document.getElementById('temaSeciciPanel');
  if (mevcutPanel) {
    mevcutPanel.remove();
    document.removeEventListener('click', _temaSeciciDisariTikla);
    return;
  }

  const mevcutAile = document.documentElement.getAttribute('data-tema-ailesi') || 'mint';
  const panel = document.createElement('div');
  panel.id = 'temaSeciciPanel';
  panel.className = 'tema-secici-panel';
  panel.innerHTML = TEMA_AILELERI.map(function(t){
    return '<div class="tema-secici-item' + (t.id === mevcutAile ? ' aktif' : '') + '" onclick="temaAileSec(\'' + t.id + '\')">' +
      '<i class="fa-solid ' + t.ikon + '"></i><span>' + t.ad + '</span>' +
      (t.id === mevcutAile ? '<i class="fa-solid fa-check tema-secici-tik"></i>' : '') +
    '</div>';
  }).join('');
  document.body.appendChild(panel);

  if (tetikleyiciEl) {
    const rect = tetikleyiciEl.getBoundingClientRect();
    if (rect.top > window.innerHeight / 2) {
      panel.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
      panel.style.top = 'auto';
    } else {
      panel.style.top = (rect.bottom + 6) + 'px';
      panel.style.bottom = 'auto';
    }
    panel.style.left = rect.left + 'px';
    panel.style.minWidth = Math.max(rect.width, 140) + 'px';
  }

  setTimeout(function(){ document.addEventListener('click', _temaSeciciDisariTikla); }, 0);
}

function _temaSeciciDisariTikla(e) {
  const panel = document.getElementById('temaSeciciPanel');
  if (panel && !panel.contains(e.target)) {
    panel.remove();
    document.removeEventListener('click', _temaSeciciDisariTikla);
  }
}

/**
 * Belirli bir tema ailesini seç ve uygula (light/dark modu değiştirmez)
 */
function temaAileSec(aileId) {
  document.documentElement.setAttribute('data-tema-ailesi', aileId);
  localStorage.setItem('bba-tema-ailesi', aileId);
  temaButonlariGuncelle(document.documentElement.getAttribute('data-theme') || 'light');
  const panel = document.getElementById('temaSeciciPanel');
  if (panel) {
    panel.remove();
    document.removeEventListener('click', _temaSeciciDisariTikla);
  }
}

/**
 * Tüm tema butonlarının ikon ve yazısını güncelle
 */
function temaButonlariGuncelle(tema) {
  const mevcutAile = document.documentElement.getAttribute('data-tema-ailesi') || 'mint';
  const aileBilgi = TEMA_AILELERI.find(function(t){ return t.id === mevcutAile }) || TEMA_AILELERI[0];

  // Sidebar butonu — aktif tema AİLESİNİN adını gösterir
  const sidebarBtn = document.getElementById('temaToggleSidebar');
  if (sidebarBtn) {
    sidebarBtn.innerHTML = '<i class="fa-solid ' + aileBilgi.ikon + '"></i> ' + aileBilgi.ad + ' Tema';
  }

  // Topbar butonu — açık/koyu MODU gösterir (eskisi gibi ay/güneş)
  const topbarBtn = document.getElementById('temaToggleTopbar');
  if (topbarBtn) {
    if (tema === 'dark') {
      topbarBtn.innerHTML = '<i class="fa-solid fa-sun"></i>';
      topbarBtn.title = 'Açık Temaya Geç';
    } else {
      topbarBtn.innerHTML = '<i class="fa-solid fa-moon"></i>';
      topbarBtn.title = 'Koyu Temaya Geç';
    }
  }

  // Sidebar / login logosu — her tema ailesinin kendi vurgu renginde
  // tek-renkli bir versiyonu var (koyu/açık zeminde okunaklı olması için).
  // İstisna: Mint temasının light modunda orijinal iki tonlu marka logosu kullanılır.
  document.querySelectorAll('.sidebar-logo img, .login-side img').forEach(function(img){
    if (mevcutAile === 'mint' && tema !== 'dark') {
      if (img.dataset.origSrc) img.src = img.dataset.origSrc;
      return;
    }
    if (!img.dataset.origSrc) img.dataset.origSrc = img.src;
    img.src = '/assets/logo/bba-logo-' + mevcutAile + '.png';
  });
}

/* ---- BBA TOPBAR AVATAR — Giriş yapan kullanıcının baş harfleri ---- */

// Rol adları (tooltipi güzelleştirmek için)
const _ROL_ADLARI = {
  admin: 'Admin',
  hazirlayan: 'Hazırlayan',
  goruntuleyici: 'Görüntüleyici',
  sayim: 'Sayım Personeli'
};

// Kullanıcı bilgisini bir kere çekip cache'le — aynı sekmede tekrar tekrar API çağrısı yapmayalım
let _kullaniciBilgiCache = null;

async function _kullaniciBilgiAl() {
  if (_kullaniciBilgiCache) return _kullaniciBilgiCache;
  try {
    const r = await fetch('/api/kullanici-bilgi');
    if (!r.ok) return null;
    const b = await r.json();
    _kullaniciBilgiCache = b;
    return b;
  } catch (e) {
    return null;
  }
}

/**
 * Sayfadaki tüm .topbar-avatar elementlerini giriş yapan kullanıcının
 * baş harfleriyle güncelle. Tooltip'e tam ad + rol yazılır.
 * Hata olursa sessizce devam eder (avatar default değerinde kalır).
 */
async function topbarAvatarGuncelle() {
  const avatarlar = document.querySelectorAll('.topbar-avatar');
  if (!avatarlar.length) return;

  const b = await _kullaniciBilgiAl();
  if (!b || !b.ad) return;

  // "Berkcan Burak Akar" → "BB"  (ilk iki kelimenin baş harfi)
  // "Ahmet"             → "A"
  // "Mehmet Çakır"      → "MÇ"
  const harfler = String(b.ad)
    .split(/\s+/)
    .filter(Boolean)
    .map(w => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  // Tooltip — Türkçe karakterleri korur
  let tip = b.ad;
  if (b.rol && _ROL_ADLARI[b.rol]) {
    tip += ' (' + _ROL_ADLARI[b.rol] + ')';
  }

  avatarlar.forEach(av => {
    if (harfler) av.innerText = harfler;
    av.title = tip;
  });
}

// Sayfa yüklenince butonları ve avatarı güncelle
document.addEventListener('DOMContentLoaded', function() {
  const tema = localStorage.getItem('bba-tema') || 'light';
  temaButonlariGuncelle(tema);
  topbarAvatarGuncelle();
});