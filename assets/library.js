(function () {
  'use strict';
  var videos = [], selected = 'all';
  var grid = document.getElementById('cardGrid');
  var search = document.getElementById('librarySearch');
  var category = document.getElementById('categoryFilter');
  var escape = App.escapeHtml;

  function render() {
    grid.replaceChildren();
    var query = search.value.trim().toLowerCase();
    var visible = videos.filter(function (v) {
      return (selected === 'all' || (v.category || '未分类') === selected) &&
        [v.title, v.speaker, v.summary_zh, v.summary_en, (v.tags || []).join(' ')].join(' ').toLowerCase().includes(query);
    });
    visible.forEach(function (v) {
      var card = document.createElement('article');
      card.className = 'card';
      card.dataset.category = v.category || '未分类';
      card.innerHTML = '<div class="card-header"><div class="card-eyebrow"><span class="card-category">' +
        escape(v.category || '未分类') + '</span></div><h3>' + escape(v.title) +
        '</h3><div class="card-speaker">' + escape(v.speaker || '讲者待补充') + '</div></div>' +
        '<div class="card-body">' +
        (v.summary_zh ? '<div class="summary-block"><div class="summary-lang">中文摘要</div><p class="summary-text">' + escape(v.summary_zh) + '</p></div>' : '') +
        (v.summary_en ? '<div class="summary-block"><div class="summary-lang">English Summary</div><p class="summary-text en">' + escape(v.summary_en) + '</p></div>' : '') + '</div>' +
        '<div class="card-meta"><span class="meta-item">⏱ ' + (v.duration_s ? App.fmtTime(v.duration_s) : '时长待确认') +
        '</span><span class="meta-item">发布时间 ' + escape(v.published || '未知') +
        '</span><span class="meta-item">' + (v.transcript_status === 'missing' ? '待转录 · 可播放' : '转录已就绪') + '</span></div>' +
        '<div class="card-tags">' + (v.tags || []).map(function (tag) { return '<span class="tag">' + escape(tag) + '</span>'; }).join('') +
        '</div><div class="card-actions"><a class="btn btn-primary" href="./player.html?video=' + v.id +
        '">▶ ' + (v.transcript_status === 'missing' ? '播放' : '开始学习') + '</a>' +
        (v.transcript_status === 'missing' ? '<a class="btn" href="./manage.html">补充转录</a>' :
        '<a class="btn" href="./transcript.html?video=' + v.id + '">📄 转录</a>') + '</div>';
      if (v.youtube) {
        var source = document.createElement('a');
        source.className = 'btn btn-yt'; source.href = v.youtube;
        source.target = '_blank'; source.rel = 'noopener'; source.textContent = '原始来源 ↗';
        card.querySelector('.card-actions').appendChild(source);
      }
      grid.appendChild(card);
    });
    if (!visible.length) grid.textContent = videos.length ? '没有符合条件的视频。' : '视频库为空，请在内容管理中导入。';
    document.getElementById('visibleCount').textContent = '显示 ' + visible.length + ' / ' + videos.length + ' 个演讲';
  }
  search.addEventListener('input', render);
  category.addEventListener('change', function () { selected = category.value; render(); });
  App.apiGet('/api/videos').then(function (data) {
    videos = data.videos;
    var categories = Array.from(new Set(videos.map(function (v) { return v.category || '未分类'; }))).sort();
    categories.forEach(function (c) { category.add(new Option(c, c)); });
    document.getElementById('talkCount').textContent = videos.length;
    document.getElementById('categoryCount').textContent = categories.length;
    document.getElementById('catalogIssues').textContent = data.issues.length ?
      data.issues.length + ' 项内容校验失败，请打开内容管理查看。' : '';
    render();
  }).catch(function (error) { grid.textContent = '无法读取视频库：' + error.message; });
})();
