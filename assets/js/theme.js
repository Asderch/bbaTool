/* ---- BBA TEMA TOGGLE SİSTEMİ ---- */

(function(){
  // Sayfa yüklenmeden önce temayı uygula (flash önleme)
  const kayitliTema = localStorage.getItem('bba-tema') || 'dark';
  document.documentElement.setAttribute('data-theme', kayitliTema);
})();

/**
 * Tema değiştir (dark ↔ light)
 */
function temaToggle() {
  const html = document.documentElement;
  const mevcutTema = html.getAttribute('data-theme') || 'dark';
  const yeniTema = mevcutTema === 'dark' ? 'light' : 'dark';

  html.setAttribute('data-theme', yeniTema);
  localStorage.setItem('bba-tema', yeniTema);

  // Buton ikonlarını güncelle
  temaButonlariGuncelle(yeniTema);
}

/**
 * Tüm tema butonlarının ikon ve yazısını güncelle
 */
function temaButonlariGuncelle(tema) {
  // Sidebar butonu
  const sidebarBtn = document.getElementById('temaToggleSidebar');
  if (sidebarBtn) {
    if (tema === 'dark') {
      sidebarBtn.innerHTML = '<i class="fa-solid fa-sun"></i> Açık Tema';
    } else {
      sidebarBtn.innerHTML = '<i class="fa-solid fa-moon"></i> Koyu Tema';
    }
  }

  // Topbar butonu
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
  const tema = localStorage.getItem('bba-tema') || 'dark';
  temaButonlariGuncelle(tema);
  topbarAvatarGuncelle();
});