(function () {
  'use strict';
  var button = document.getElementById('exportData');
  var status = document.getElementById('dataStatus');
  App.getVocab().then(function (entries) {
    document.getElementById('dataStats').textContent = entries.length + ' 条生词记录';
  }).catch(function (error) { status.textContent = error.message; });
  button.addEventListener('click', function () {
    button.disabled = true;
    status.textContent = '正在准备备份…';
    var last;
    try { last = JSON.parse(localStorage.getItem('ted:last') || 'null'); }
    catch (_) {
      status.textContent = '当前浏览器的续播记录无法读取，请先在播放器重新保存位置后重试。';
      button.disabled = false; return;
    }
    App.apiPost('/api/learning-data/export', {last:last}).then(function (data) {
      var blob = new Blob([JSON.stringify(data.backup, null, 2)], {type:'application/json'});
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url; link.download = 'tedlib-learning-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      status.textContent = '已导出 ' + data.backup.vocab.entries.length + ' 条生词记录、' +
        Object.keys(data.backup.review.words).length + ' 个复习安排。' +
        (data.backup.resume.last ? '已包含续播位置。' : '当前浏览器暂无续播位置。') +
        (data.backup.warnings.length ? '\n' + data.backup.warnings.join('\n') : '');
    }).catch(function (error) { status.textContent = '导出失败：' + error.message; })
      .finally(function () { button.disabled = false; });
  });
})();
