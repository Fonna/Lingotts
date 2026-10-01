(function () {
  'use strict';
  var key = new URLSearchParams(location.search).get('video');
  var status = document.getElementById('readingStatus');
  if (!key) { status.textContent = '请从视频库选择一个视频。'; return; }
  App.apiGet('/api/transcript?video=' + encodeURIComponent(key)).then(function (data) {
    document.title = data.video.title + ' · 转录';
    document.getElementById('readingTitle').textContent = data.video.title;
    document.getElementById('readingSpeaker').textContent = data.video.speaker || '';
    var player = document.getElementById('readingPlayer');
    player.href = './player.html?video=' + data.video.id;
    player.hidden = false;
    var container = document.getElementById('readingLines');
    if (!data.segments.length) {
      status.textContent = '待转录：这个视频还没有字幕，可以先打开播放器观看。补充带时间戳字幕后，刷新此页即可阅读。';
      return;
    }
    data.segments.forEach(function (segment) {
      var row = document.createElement('div'); row.className = 'reading-line'; row.id = segment.id;
      var link = document.createElement('a');
      link.href = player.href + '&t=' + segment.start;
      link.textContent = App.fmtTime(segment.start); link.title = '播放这一句';
      var text = document.createElement('p'); text.textContent = segment.text;
      row.append(link, text); container.appendChild(row);
    });
    status.textContent = data.segments.length + ' 段原文 · 点击时间可跳回播放';
  }).catch(function (error) {
    document.getElementById('readingTitle').textContent = '无法打开转录'; status.textContent = error.message;
  });
})();
