/* ---- BBA POPUP & TOAST SİSTEMİ ---- */

(function(){
  // Toast container
  if(!document.querySelector('.toast-container')){
    const tc = document.createElement('div')
    tc.className = 'toast-container'
    document.body.appendChild(tc)
  }

  // Popup overlay
  if(!document.getElementById('bbaPopup')){
    const ol = document.createElement('div')
    ol.className = 'popup-overlay'
    ol.id = 'bbaPopup'
    ol.innerHTML = `
      <div class="popup-kutu">
        <div class="popup-ust">
          <div class="popup-ikon" id="popupIkon"><i class="fa-solid fa-check"></i></div>
          <div class="popup-baslik" id="popupBaslik"></div>
          <div class="popup-mesaj" id="popupMesaj"></div>
        </div>
        <div class="popup-alt" id="popupBtnler"></div>
      </div>`
    document.body.appendChild(ol)
  }
})();

/**
 * Toast göster
 * @param {string} mesaj
 * @param {string} tip - "basari"|"hata"|"uyari"|"bilgi"
 * @param {number} sure - ms (default 3000)
 */
function toast(mesaj, tip = "bilgi", sure = 3000){
  const container = document.querySelector('.toast-container')
  const t = document.createElement('div')
  t.className = 'toast ' + tip

  const ikonlar = {
    basari: 'fa-solid fa-circle-check',
    hata: 'fa-solid fa-circle-xmark',
    uyari: 'fa-solid fa-triangle-exclamation',
    bilgi: 'fa-solid fa-circle-info'
  }

  t.innerHTML = `<i class="${ikonlar[tip] || ikonlar.bilgi}"></i><span>${mesaj}</span>`
  container.appendChild(t)

  setTimeout(() => {
    t.style.animation = 'toastOut .3s ease forwards'
    setTimeout(() => t.remove(), 300)
  }, sure)
}

/**
 * Bilgilendirme popup (alert yerine)
 * @param {string} mesaj
 * @param {string} baslik
 * @param {string} tip - "basari"|"hata"|"uyari"|"bilgi"
 * @returns {Promise}
 */
function bildirim(mesaj, baslik = "", tip = "bilgi"){
  return new Promise(resolve => {
    const ikonlar = {
      basari: 'fa-solid fa-circle-check',
      hata: 'fa-solid fa-circle-xmark',
      uyari: 'fa-solid fa-triangle-exclamation',
      bilgi: 'fa-solid fa-circle-info',
      soru: 'fa-solid fa-circle-question'
    }

    const basliklar = {
      basari: 'Başarılı',
      hata: 'Hata',
      uyari: 'Uyarı',
      bilgi: 'Bilgi'
    }

    document.getElementById('popupIkon').className = 'popup-ikon ' + tip
    document.getElementById('popupIkon').innerHTML = `<i class="${ikonlar[tip] || ikonlar.bilgi}"></i>`
    document.getElementById('popupBaslik').innerText = baslik || basliklar[tip] || 'Bilgi'
    document.getElementById('popupMesaj').innerText = mesaj

    document.getElementById('popupBtnler').innerHTML = `
      <button class="popup-btn onayla" id="popupTamam">Tamam</button>`

    document.getElementById('bbaPopup').classList.add('goster')

    document.getElementById('popupTamam').onclick = () => {
      document.getElementById('bbaPopup').classList.remove('goster')
      resolve(true)
    }
  })
}

/**
 * Onay popup (confirm yerine)
 * @param {string} mesaj
 * @param {string} baslik
 * @param {object} opts - {onayText, iptalText, tip}
 * @returns {Promise<boolean>}
 */
function onayla(mesaj, baslik = "Emin misiniz?", opts = {}){
  return new Promise(resolve => {
    const tip = opts.tip || "soru"
    const onayText = opts.onayText || "Onayla"
    const iptalText = opts.iptalText || "İptal"
    const tehlike = opts.tehlike || false

    const ikonlar = {
      basari: 'fa-solid fa-circle-check',
      hata: 'fa-solid fa-circle-xmark',
      uyari: 'fa-solid fa-triangle-exclamation',
      bilgi: 'fa-solid fa-circle-info',
      soru: 'fa-solid fa-circle-question'
    }

    document.getElementById('popupIkon').className = 'popup-ikon ' + tip
    document.getElementById('popupIkon').innerHTML = `<i class="${ikonlar[tip] || ikonlar.soru}"></i>`
    document.getElementById('popupBaslik').innerText = baslik
    document.getElementById('popupMesaj').innerText = mesaj

    document.getElementById('popupBtnler').innerHTML = `
      <button class="popup-btn iptal" id="popupIptal">${iptalText}</button>
      <button class="popup-btn ${tehlike ? 'tehlike' : 'onayla'}" id="popupOnayla">${onayText}</button>`

    document.getElementById('bbaPopup').classList.add('goster')

    document.getElementById('popupIptal').onclick = () => {
      document.getElementById('bbaPopup').classList.remove('goster')
      resolve(false)
    }

    document.getElementById('popupOnayla').onclick = () => {
      document.getElementById('bbaPopup').classList.remove('goster')
      resolve(true)
    }
  })
}