/* Corbitraj - shared navigation. Included by every page with <script src="/nav.js">. */
(function () {
  const path = window.location.pathname.replace(/\/$/, '') || '/';
  const LANG_KEY = 'corbitraj_lang_v2';
  const listeners = [];

  function getLang() {
    return localStorage.getItem(LANG_KEY) === 'tr' ? 'tr' : 'en';
  }

  const style = document.createElement('style');
  style.textContent = `
    #c-nav {
      background: #0f1623;
      border-bottom: 1px solid #1e293b;
      padding: 7px 20px;
      display: flex;
      align-items: center;
      gap: 6px;
      flex-shrink: 0;
      font-family: system-ui, -apple-system, sans-serif;
      z-index: 50;
      position: relative;
    }
    #c-nav .c-logo {
      font-size: 16px; font-weight: 800; color: #10b981;
      text-decoration: none; margin-right: 14px; white-space: nowrap;
    }
    #c-nav .c-logo span { color: #64748b; font-weight: 400; font-size: 11px; margin-left: 5px; }
    .c-link {
      color: #64748b; text-decoration: none;
      padding: 5px 13px; border-radius: 6px;
      font-size: 12px; font-weight: 600;
      border: 1px solid transparent;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .c-link:hover { color: #e2e8f0; background: #161e2e; border-color: #1e293b; }
    .c-link.c-active {
      color: #10b981; background: #10b98118; border-color: #10b98140;
    }
  `;
  document.head.appendChild(style);

  const LINKS = {
    tr: [
      ['/', '⊞ Pano'],
      ['/trade', '⚡ İşlem'],
      ['/wallet', '💰 Cüzdan'],
      ['/pairs', '📋 Pairler'],
      ['/api-keys', '🔑 API'],
      ['/settings', '⚙ Ayarlar'],
    ],
    en: [
      ['/', '⊞ Dashboard'],
      ['/trade', '⚡ Trade'],
      ['/wallet', '💰 Wallet'],
      ['/pairs', '📋 Pairs'],
      ['/api-keys', '🔑 API'],
      ['/settings', '⚙ Settings'],
    ],
  };

  function renderNav() {
    const old = document.getElementById('c-nav');
    if (old) old.remove();
    document.documentElement.lang = getLang();

    const nav = document.createElement('header');
    nav.id = 'c-nav';
    nav.innerHTML =
      `<a class="c-logo" href="/">◈ CORBITRAJ</a>` +
      LINKS[getLang()].map(([href, label]) =>
        `<a href="${href}" class="c-link${path === href ? ' c-active' : ''}">${label}</a>`
      ).join('');

    document.body.insertBefore(nav, document.body.firstChild);
  }

  function notifyLanguageChange() {
    const lang = getLang();
    listeners.forEach(fn => fn(lang));
    window.dispatchEvent(new CustomEvent('corbitraj:language-applied', { detail: { lang } }));
  }

  function translateBackendText(value) {
    if (getLang() !== 'en') return value;
    let text = String(value ?? '');
    const replacements = [
      ['Borsalara bağlanılıyor...', 'Connecting to exchanges...'],
      ['İlk tarama başlıyor...', 'Starting first scan...'],
      ['Borsa bulunamadı', 'Exchange not found'],
      ['API anahtarı girilmemiş — /api-keys sayfasından ekleyin.', 'API key not entered - add it on the /api-keys page.'],
      ['API anahtarı girilmemiş', 'API key not entered'],
      ["DRY_RUN aktif — config.py'de DRY_RUN = False yapın", "DRY_RUN is active - set DRY_RUN = False in config.py"],
      ['Geçersiz miktar', 'Invalid amount'],
      ['Coin açıklaması bulunamadı', 'Coin description not found'],
      ['Geçersiz dil', 'Invalid language'],
      ['Hata', 'Error'],
      ['fırsat', 'opportunities'],
      ['sembol tarandı', 'symbols scanned'],
      ['Bağlantılar kapatıldı.', 'Connections closed.'],
    ];
    for (const [from, to] of replacements) {
      text = text.split(from).join(to);
    }
    return text;
  }

  window.CorbitrajI18n = {
    getLang,
    setLang(lang) {
      localStorage.setItem(LANG_KEY, lang === 'tr' ? 'tr' : 'en');
      renderNav();
      notifyLanguageChange();
    },
    onChange(fn) {
      if (typeof fn !== 'function') return;
      listeners.push(fn);
      fn(getLang());
    },
    translateBackendText,
  };

  window.addEventListener('storage', event => {
    if (event.key === LANG_KEY) {
      renderNav();
      notifyLanguageChange();
    }
  });

  window.addEventListener('corbitraj:language', event => {
    window.CorbitrajI18n.setLang(event.detail?.lang);
  });

  renderNav();
})();
