/* =========================================================================
   i18n — traducción de la interfaz (español ⇄ otros idiomas)
   -------------------------------------------------------------------------
   El idioma BASE del código es el español: todos los textos están escritos
   en español directamente en el HTML y en los .js. Por eso 'es' no necesita
   diccionario (coste cero) y solo traducimos HACIA otros idiomas con un
   diccionario { "texto en español": "texto traducido" }.

   La traducción se aplica recorriendo el DOM (nodos de texto + atributos
   placeholder/title/aria-label) y con un MutationObserver que traduce también
   el contenido generado dinámicamente por los demás scripts. Así no hace falta
   envolver cientos de cadenas a mano: el propio texto español es la clave.

   Los diccionarios viven en /static/i18n/<code>.json y se cargan bajo demanda.
   Cambiar de idioma persiste en localStorage y recarga la página (re-render
   limpio). Idiomas futuros (fr, de, zh, ko, ja, eu, ca) solo requieren añadir
   su .json y su entrada en LANGS.
   ========================================================================= */
(function () {
  "use strict";

  var LS_KEY = "bt_lang";
  var BASE = "es";
  var LANG = localStorage.getItem(LS_KEY) || BASE;

  // Idiomas ofrecidos en el selector. De momento ES/EN; el resto, "próximamente".
  // `cc` = código de bandera (país/región) para el icono. Windows no dibuja los emoji
  // de bandera (los muestra como letras), así que usamos imágenes reales (flagcdn).
  var LANGS = [
    { code: "es", label: "Español", cc: "es" },
    { code: "eu", label: "Euskera", cc: "es-pv", flag: "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_the_Basque_Country.svg?width=40" },
    { code: "ca", label: "Català", cc: "es-ct", flag: "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Catalonia.svg?width=40" },
    { code: "gl", label: "Galego", cc: "es-ga", flag: "https://commons.wikimedia.org/wiki/Special:FilePath/Flag_of_Galicia.svg?width=40" },
    { code: "en", label: "English", cc: "gb" },
    { code: "fr", label: "Français", cc: "fr" },
    { code: "de", label: "Deutsch", cc: "de" },
    { code: "it", label: "Italiano", cc: "it" },
    { code: "fi", label: "Suomi", cc: "fi", soon: true },
    { code: "nl", label: "Nederlands", cc: "nl", soon: true },
    { code: "ru", label: "Русский", cc: "ru", soon: true },
    { code: "pl", label: "Polski", cc: "pl", soon: true },
    { code: "zh", label: "中文", cc: "cn", soon: true },
    { code: "ko", label: "한국어", cc: "kr", soon: true },
    { code: "ja", label: "日本語", cc: "jp", soon: true },
    { code: "ar", label: "العربية", cc: "sa", soon: true },
    { code: "tr", label: "Türkçe", cc: "tr", soon: true },
    { code: "hi", label: "हिन्दी", cc: "in", soon: true },
    { code: "th", label: "ไทย", cc: "th", soon: true },
    { code: "id", label: "Bahasa Indonesia", cc: "id", soon: true },
    { code: "bn", label: "বাংলা", cc: "bd", soon: true },
    { code: "vi", label: "Tiếng Việt", cc: "vn", soon: true },
    { code: "pa", label: "ਪੰਜਾਬੀ", cc: "in", soon: true },
  ];

  /* Icono de bandera (imagen). Si la imagen falla, muestra el código como texto. */
  function flagImg(l, cls) {
    var src = l.flag || ('https://flagcdn.com/w40/' + l.cc + '.png');   // override para banderas regionales (Ikurriña, Senyera, Galicia)
    return '<img class="' + cls + '" src="' + src + '" alt="" ' +
      'loading="lazy" onerror="this.style.display=\'none\';var s=this.nextElementSibling;if(s)s.style.display=\'inline-flex\'">' +
      '<span class="lang-code-fb" style="display:none">' + l.code.toUpperCase() + '</span>';
  }

  var _dicts = {};   // { en: {es: en}, ... } ya cargados
  var dict = null;   // diccionario activo (null si idioma base)
  var _patterns = [];// reglas por patrón (para cadenas interpoladas), compiladas del diccionario
  var observer = null;

  window.currentLang = function () { return LANG; };
  window.I18N_LANGS = LANGS;

  function norm(s) { return s.replace(/\s+/g, " ").trim(); }

  /* Compila las reglas de patrón del diccionario (clave especial "__patterns__":
     lista de [regex_es, reemplazo_en] con grupos $1,$2… para las partes variables
     de las cadenas interpoladas, p.ej. ["^(\\d+) seleccionados$", "$1 selected"]). */
  function compilePatterns(d) {
    _patterns = [];
    var raw = d && d.__patterns__;
    if (!Array.isArray(raw)) return;
    for (var i = 0; i < raw.length; i++) {
      var p = raw[i];
      if (!p || p.length < 2) continue;
      try { _patterns.push({ re: new RegExp(p[0]), sub: p[1] }); }
      catch (e) { /* patrón inválido: se ignora */ }
    }
  }

  /* Traduce una cadena suelta conservando los espacios de alrededor. Primero busca
     coincidencia exacta (rápida y segura); si falla, prueba las reglas por patrón. */
  function tr(s) {
    if (!dict || s == null) return s;
    var key = norm(s);
    if (!key) return s;
    var core = dict[key];
    if (core == null) {
      for (var i = 0; i < _patterns.length; i++) {
        if (_patterns[i].re.test(key)) { core = key.replace(_patterns[i].re, _patterns[i].sub); break; }
      }
    }
    if (core == null || core === key) return s;
    var pre = (s.match(/^\s*/) || [""])[0];
    var post = (s.match(/\s*$/) || [""])[0];
    return pre + core + post;
  }
  // Para textos construidos en JS que se quieran traducir explícitamente.
  window.t = function (s) { return tr(s); };

  var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, CODE: 1, PRE: 1, INPUT: 1 };
  var ATTRS = ["placeholder", "title", "aria-label"];

  function translateTextNode(node) {
    var v = node.nodeValue;
    if (!v || !v.trim()) return;
    var out = tr(v);
    if (out !== v) node.nodeValue = out;
  }

  function translateAttrs(el) {
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i];
      if (el.hasAttribute && el.hasAttribute(a)) {
        var v = el.getAttribute(a);
        var out = tr(v);
        if (out !== v) el.setAttribute(a, out);
      }
    }
  }

  function skipParent(p) {
    while (p && p.nodeType === 1) {
      if (SKIP_TAGS[p.tagName] || (p.dataset && p.dataset.i18nSkip != null)) return true;
      p = p.parentNode;
    }
    return false;
  }

  /* Recorre un subárbol traduciendo nodos de texto y atributos. */
  function walk(root) {
    if (!dict || !root) return;
    if (root.nodeType === 3) {  // TEXT_NODE
      if (!skipParent(root.parentNode)) translateTextNode(root);
      return;
    }
    if (root.nodeType !== 1) return;  // solo elementos
    if (SKIP_TAGS[root.tagName] || (root.dataset && root.dataset.i18nSkip != null)) return;
    translateAttrs(root);
    var tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        return skipParent(n.parentNode) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      }
    });
    var n;
    while ((n = tw.nextNode())) translateTextNode(n);
    var els = root.querySelectorAll("[placeholder],[title],[aria-label]");
    for (var i = 0; i < els.length; i++) translateAttrs(els[i]);
  }
  window.translateTree = walk;

  function startObserver() {
    if (observer || !dict) return;
    observer = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === "characterData") {
          if (!skipParent(m.target.parentNode)) translateTextNode(m.target);
        } else {
          for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  function loadDict(code) {
    if (code === BASE) return Promise.resolve(null);
    if (_dicts[code]) return Promise.resolve(_dicts[code]);
    // Base: el .json estático sembrado. Overrides: traducciones de la comunidad (Rosetta),
    // que ganan sobre lo sembrado. Se fusionan exactas y patrones (dedup por regex).
    var staticP = fetch("/static/i18n/" + code + ".json")
      .then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; });
    var commP = fetch("/api/i18n/" + code)
      .then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; });
    return Promise.all([staticP, commP]).then(function (arr) {
      var base = arr[0] || {}, comm = arr[1] || {}, merged = {}, k;
      for (k in base) if (k !== "__patterns__") merged[k] = base[k];
      var cex = comm.exact || {};
      for (k in cex) if (cex[k]) merged[k] = cex[k];              // la comunidad gana
      var pats = [], seen = {}, i, cp = comm.patterns || [], sp = base.__patterns__ || [];
      for (i = 0; i < cp.length; i++) if (cp[i] && !seen[cp[i][0]]) { seen[cp[i][0]] = 1; pats.push(cp[i]); }
      for (i = 0; i < sp.length; i++) if (sp[i] && !seen[sp[i][0]]) { seen[sp[i][0]] = 1; pats.push(sp[i]); }
      pats.sort(function (a, b) { return b[0].length - a[0].length; });  // más específicos primero
      merged.__patterns__ = pats;
      _dicts[code] = merged;
      return merged;
    });
  }

  /* Cambia el idioma: persiste y recarga (re-render limpio en el nuevo idioma). */
  window.setLang = function (code) {
    if (!code || code === LANG) { closeLangMenu(); return; }
    localStorage.setItem(LS_KEY, code);
    location.reload();
  };

  // ---- Selector de idioma (bandera, a la izquierda del menú de jugador) ----
  function langByCode(code) {
    for (var i = 0; i < LANGS.length; i++) if (LANGS[i].code === code) return LANGS[i];
    return LANGS[0];
  }
  function closeLangMenu() {
    var m = document.getElementById("lang-menu");
    if (m) m.classList.remove("open");
    var t = document.getElementById("lang-toggle");
    if (t) t.classList.remove("open");
  }
  function injectSwitcher() {
    var host = document.getElementById("lang-switch");
    if (!host) {
      // Fallback: crear el contenedor al inicio de la barra de herramientas.
      var toolbar = document.querySelector("header .toolbar");
      if (!toolbar) return;
      host = document.createElement("div");
      host.id = "lang-switch";
      toolbar.insertBefore(host, toolbar.firstChild);
    }
    var cur = langByCode(LANG);
    var opts = LANGS.map(function (l) {
      return '<button class="lang-opt' + (l.code === LANG ? " active" : "") + (l.soon ? " soon" : "") +
        '" role="option"' + (l.soon ? " disabled" : "") +
        ' onclick="' + (l.soon ? "" : "setLang('" + l.code + "')") + '">' +
        flagImg(l, "lang-flag-img") + '<span class="lang-name">' + l.label + "</span>" +
        (l.soon ? '<span class="lang-soon">Próximamente</span>' : "") + "</button>";
    }).join("");
    host.innerHTML =
      '<button class="lang-toggle" id="lang-toggle" title="Idioma / Language" aria-label="Idioma" data-i18n-skip>' +
        flagImg(cur, "lang-flag-img") +
        '<span class="lang-caret">▾</span>' +
      "</button>" +
      '<div class="lang-menu" id="lang-menu" role="listbox" data-i18n-skip>' + opts + "</div>";
    host.setAttribute("data-i18n-skip", "");
    var toggle = document.getElementById("lang-toggle");
    toggle.onclick = function (e) {
      e.stopPropagation();
      var menu = document.getElementById("lang-menu");
      var open = menu.classList.toggle("open");
      toggle.classList.toggle("open", open);
    };
    document.addEventListener("click", function (e) {
      if (!host.contains(e.target)) closeLangMenu();
    });
  }

  function init() {
    injectSwitcher();
    loadDict(LANG).then(function (d) {
      dict = d;
      if (dict) { compilePatterns(dict); walk(document.body); startObserver(); }
    });
    if (LANG !== BASE) document.documentElement.setAttribute("lang", LANG);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
