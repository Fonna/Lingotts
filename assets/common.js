/* ===== TedLib shared helpers: nav, theme, API ===== */
(function () {
  'use strict';

  var NAV_LINKS = [
    { href: 'index.html', label: '🎧 学习中心' },
    { href: 'review.html', label: '🗂️ 复习' },
    { href: 'library.html', label: '📚 视频库' },
    { href: 'vocab.html', label: '📝 生词本' },
    { href: 'manage.html', label: '内容管理' }
  ];

  function currentFile() {
    var p = location.pathname.split('/').pop();
    return p === '' ? 'index.html' : p;
  }

  function renderNav() {
    var cur = currentFile();
    var nav = document.createElement('nav');
    nav.className = 'app-nav';
    var html = '<a class="brand" href="index.html">TED <span>Learner</span></a>';
    NAV_LINKS.forEach(function (l) {
      html += '<a class="nav-link' + (cur === l.href ? ' active' : '') +
              '" href="' + l.href + '">' + l.label + '</a>';
    });
    html += '<span class="nav-spacer"></span>';
    nav.innerHTML = html;
    document.body.insertBefore(nav, document.body.firstChild);
  }

  // Apply saved theme ASAP
  if (localStorage.getItem('ted-theme') === 'cool') {
    document.body.dataset.theme = 'cool';
  }

  function fmtTime(seconds) {
    var s = Math.floor(Number(seconds) || 0);
    var m = Math.floor(s / 60);
    s = s % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function highlightWord(sentence, word) {
    sentence = String(sentence || '');
    word = String(word || '');
    if (!word) return escapeHtml(sentence);
    var escaped = word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var re = new RegExp(escaped, 'ig');
    var html = '', last = 0, match;
    while ((match = re.exec(sentence)) !== null) {
      html += escapeHtml(sentence.slice(last, match.index)) +
        '<mark>' + escapeHtml(match[0]) + '</mark>';
      last = re.lastIndex;
    }
    return html + escapeHtml(sentence.slice(last));
  }

  function request(url, options) {
    return fetch(url, options).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(data.error || ('HTTP ' + r.status));
        });
      }
      return r.json();
    });
  }

  function field(label, value) {
    if (!value) return '';
    return '<div class="dict-field"><span class="dict-label">' + label + '</span>' +
      '<span>' + escapeHtml(value).replace(/\n/g, '<br>') + '</span></div>';
  }

  function dictionaryHtml(entry) {
    return '<div class="dict-source">ECDICT · 本地词典</div>' +
      field('音标', entry.phonetic) + field('词性', entry.pos) +
      field('中文', entry.zh) + field('英文', entry.en);
  }

  function analysisHtml(data) {
    var html = '<div class="dict-source">语境深度解析 · 火山方舟</div>' +
      field('音标', data.phonetic) + field('词性', data.pos) +
      field('中文', data.zh) + field('英文', data.en) +
      field('词源', data.root) + field('搭配', data.collocations) +
      field('记忆', data.memory);
    if (Array.isArray(data.examples) && data.examples.length) {
      html += field('例句', data.examples.join('\n'));
    }
    return html;
  }

  window.App = {
    fmtTime: fmtTime,
    escapeHtml: escapeHtml,
    highlightWord: highlightWord,
    toggleTheme: function (btn) {
      var cool = document.body.dataset.theme !== 'cool';
      document.body.dataset.theme = cool ? 'cool' : 'warm';
      localStorage.setItem('ted-theme', document.body.dataset.theme);
      if (btn) btn.textContent = cool ? '☀️ Warm' : '🌙 Cool';
    },
    apiGet: function (url) {
      return request(url);
    },
    apiPost: function (url, data) {
      return request(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
    },
    apiDelete: function (url) {
      return request(url, { method: 'DELETE' });
    },
    lookupWord: function (word) {
      return request('/api/dictionary?word=' + encodeURIComponent(word))
        .then(function (data) { return data.entry; });
    },
    analyzeWord: function (word, sentence) {
      return request('/api/word-analysis', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word: word, sentence: sentence || '' })
      }).then(function (data) { return data.analysis; });
    },
    cachedAnalysis: function (word, sentence) {
      return request('/api/word-analysis?word=' + encodeURIComponent(word) +
        '&sentence=' + encodeURIComponent(sentence || ''))
        .then(function (data) { return data.analysis; });
    },
    getReview: function () {
      return request('/api/review');
    },
    gradeReview: function (word, rating, version) {
      return request('/api/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word: word, rating: rating, version: version })
      });
    },
    dictionaryHtml: dictionaryHtml,
    analysisHtml: analysisHtml,
    getVocab: function () {
      return request('/api/vocab').then(function (d) {
        return d.entries || [];
      });
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderNav);
  } else {
    renderNav();
  }
})();
