(function () {
  'use strict';
  var button = document.getElementById('exportData');
  var status = document.getElementById('dataStatus');
  function downloadBackup(backup) {
    var url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], {type:'application/json'}));
    var link = document.createElement('a'); link.href = url;
    link.download = 'tedlib-learning-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  function readLast() {
    return JSON.parse(localStorage.getItem('ted:last') || 'null');
  }
  function history() {
    var box = document.getElementById('backupHistory');
    App.apiGet('/api/learning-data/backups').then(function (data) {
      box.replaceChildren();
      if (!data.backups.length) box.textContent = '暂无恢复前备份。';
      data.backups.forEach(function (item) {
        var row = document.createElement('p'), button = document.createElement('button');
        row.textContent = new Date(item.exported_at).toLocaleString('zh-CN') + ' · ' + item.vocab_count + ' 条生词 · ' +
          ({committed:'恢复完成',rolled_back:'已回退',prepared:'恢复未完成'}[item.status] || '未知状态') + ' ';
        button.className = 'btn'; button.textContent = '下载备份';
        button.onclick = function () {
          App.apiGet('/api/learning-data/backup?id=' + encodeURIComponent(item.id)).then(function (data) {
            downloadBackup(data.backup);
          }).catch(function (error) { restoreStatus.textContent = error.message; });
        };
        row.appendChild(button); box.appendChild(row);
      });
      data.issues.forEach(function (issue) { var row = document.createElement('p'); row.textContent = issue; box.appendChild(row); });
    }).catch(function (error) { box.textContent = error.message; });
  }
  document.getElementById('refreshHistory').onclick = history;
  history();
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
      downloadBackup(data.backup);
      status.textContent = '已导出 ' + data.backup.vocab.entries.length + ' 条生词记录、' +
        Object.keys(data.backup.review.words).length + ' 个复习安排。' +
        '片段进度 ' + Object.keys(data.backup.learning.segments).length + ' 个、练习 ' + data.backup.learning.attempts.length + ' 次、计划设置 ' + Object.keys(data.backup.learning.days).length + ' 天。' +
        (data.backup.resume.last ? '已包含续播位置。' : '当前浏览器暂无续播位置。') +
        (data.backup.warnings.length ? '\n' + data.backup.warnings.join('\n') : '');
    }).catch(function (error) { status.textContent = '导出失败：' + error.message; })
      .finally(function () { button.disabled = false; });
  });
  var file = document.getElementById('importFile'), mode = document.getElementById('restoreMode');
  var previewButton = document.getElementById('previewData'), restoreButton = document.getElementById('restoreData');
  var previewBox = document.getElementById('restorePreview'), restoreStatus = document.getElementById('restoreStatus');
  var confirm = document.getElementById('confirmReplace'), beforeButton = document.getElementById('downloadBefore');
  var retryResume = document.getElementById('retryResume');
  var preview = null, source = null, request = 0, backupId = null, pendingResume = null;
  function invalidate() {
    request++; preview = null; source = null; previewBox.hidden = true; restoreButton.hidden = true;
    confirm.checked = false; previewButton.disabled = !file.files.length; restoreStatus.textContent = '';
    document.getElementById('replaceWarning').hidden = true;
    document.getElementById('restoreRule').textContent = mode.value === 'merge'
      ? '重复生词保留本地，复习、片段与计划设置取较新记录，练习按编号合并；已有续播位置保留。'
      : '生词、复习、片段进度、练习和计划设置以备份替换；续播以备份为准。旧备份未包含的活动类别保留并提示。';
  }
  file.addEventListener('change', invalidate); mode.addEventListener('change', invalidate);
  previewButton.addEventListener('click', function () {
    invalidate();
    var current = request, selected = file.files[0];
    if (!selected) return;
    if (selected.size > 16 * 1024 * 1024) { restoreStatus.textContent = '备份文件不能超过 16 MiB。'; return; }
    previewButton.disabled = true; restoreStatus.textContent = '正在校验备份…';
    selected.text().then(function (text) {
      if (current !== request) return null;
      source = text;
      return App.apiPost('/api/learning-data/preview', {text:text,mode:mode.value,last:readLast()});
    }).then(function (data) {
      if (current !== request || !data) return;
      preview = data.preview;
      var counts = preview.counts;
      previewBox.textContent = '备份时间：' + new Date(preview.exported_at).toLocaleString('zh-CN') +
        '\n当前 ' + counts.before_vocab + ' 条生词；备份 ' + counts.incoming_vocab + ' 条。' +
        '\n恢复后 ' + counts.after_vocab + ' 条生词、' + counts.after_review + ' 个复习安排。' +
        '\n片段进度 ' + counts.after_segments + ' 个；练习记录 ' + counts.after_attempts + ' 次；计划设置 ' + counts.after_days + ' 天。' +
        '\n跳过重复生词 ' + counts.duplicates + ' 条；重分配冲突 ID ' + counts.id_conflicts + ' 条；保留较新本地复习安排 ' + counts.review_kept + ' 个。' +
        '\n续播位置：' + (preview.resume.last ? (preview.resume.last.title + ' · ' + App.fmtTime(preview.resume.last.t)) : '无') +
        (preview.warnings.length ? '\n' + preview.warnings.join('\n') : '');
      previewBox.hidden = false; restoreButton.hidden = false;
      document.getElementById('replaceWarning').hidden = mode.value !== 'replace';
      restoreStatus.textContent = '校验通过，请核对预览后确认恢复。';
    }).catch(function (error) {
      if (current === request) { preview = null; restoreStatus.textContent = '预览失败：' + error.message; }
    }).finally(function () { if (current === request) previewButton.disabled = false; });
  });
  function saveResume(last) {
    if (last) localStorage.setItem('ted:last', JSON.stringify(last));
    else localStorage.removeItem('ted:last');
  }
  restoreButton.addEventListener('click', function () {
    if (!preview || !source) return;
    if (mode.value === 'replace' && !confirm.checked) { restoreStatus.textContent = '请先确认覆盖当前学习数据。'; return; }
    var last, prior, probe = 'ted:restore-probe:' + Date.now();
    try {
      prior = localStorage.getItem('ted:last'); last = readLast();
      localStorage.setItem(probe, JSON.stringify(preview.resume.last)); localStorage.removeItem(probe);
    } catch (_) {
      try { localStorage.removeItem(probe); } catch (_) {}
      restoreStatus.textContent = '当前浏览器无法保存续播位置，尚未恢复数据。请检查浏览器存储后重试。'; return;
    }
    file.disabled = mode.disabled = previewButton.disabled = restoreButton.disabled = true;
    restoreStatus.textContent = '正在保存恢复前备份并恢复数据…';
    App.apiPost('/api/learning-data/restore', {text:source,mode:mode.value,last:last,token:preview.token})
      .then(function (data) {
        backupId = data.backup_id; beforeButton.hidden = false;
        history();
        preview = null; restoreButton.hidden = true;
        var message = '已恢复 ' + data.counts.after_vocab + ' 条生词和 ' + data.counts.after_review + ' 个复习安排；恢复前备份已保存。';
        message += '\n片段进度 ' + data.counts.after_segments + ' 个，练习 ' + data.counts.after_attempts + ' 次，计划设置 ' + data.counts.after_days + ' 天。';
        try {
          if (localStorage.getItem('ted:last') !== prior) message += '\n恢复期间其他页面更新了续播位置，已保留最新位置。';
          else saveResume(data.resume.last);
        } catch (_) {
          pendingResume = data.resume.last; retryResume.hidden = false;
          message += '\n生词与复习恢复成功，续播位置写入失败，请点击重试保存。';
        }
        restoreStatus.textContent = message;
        document.getElementById('dataStats').textContent = data.counts.after_vocab + ' 条生词记录';
      }).catch(function (error) { restoreStatus.textContent = '恢复失败：' + error.message; })
      .finally(function () { file.disabled = mode.disabled = previewButton.disabled = restoreButton.disabled = false; });
  });
  beforeButton.addEventListener('click', function () {
    if (!backupId) return;
    App.apiGet('/api/learning-data/backup?id=' + encodeURIComponent(backupId)).then(function (data) {
      downloadBackup(data.backup);
    }).catch(function (error) { restoreStatus.textContent = '备份下载失败：' + error.message; });
  });
  retryResume.addEventListener('click', function () {
    try { saveResume(pendingResume); retryResume.hidden = true; restoreStatus.textContent = '续播位置已保存。'; }
    catch (_) { restoreStatus.textContent = '续播位置仍无法保存，请检查浏览器存储。'; }
  });
})();
