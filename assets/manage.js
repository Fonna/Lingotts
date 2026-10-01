(function () {
  'use strict';
  var form = document.getElementById('metadataForm'), selected = null;
  var status = document.getElementById('manageStatus'), save = document.getElementById('saveVideo');
  var list = document.getElementById('folderList'), scanButton = document.getElementById('scanFolders');
  function select(item, button) {
    selected = item; form.reset(); form.hidden = false;
    list.querySelectorAll('button').forEach(function (b) { b.classList.toggle('selected', b === button); });
    document.getElementById('formTitle').textContent = item.video_id ? '编辑视频信息' : '导入视频';
    save.textContent = item.video_id ? '保存修改' : '确认导入';
    var metadata = item.metadata || {folder:item.folder, title:item.folder, transcript_file:'transcript.txt'};
    Array.from(form.elements).forEach(function (element) {
      if (!element.name || element.name === 'media_file') return;
      element.value = element.name === 'tags' ? (metadata.tags || []).join(', ') : metadata[element.name] || '';
    });
    var media = form.elements.media_file; media.replaceChildren();
    media.add(new Option('请选择媒体文件', ''));
    (item.media_files || []).forEach(function (name) { media.add(new Option(name, name)); });
    media.value = metadata.media_file || '';
    status.textContent = item.error || (item.video_id ? '固定 ID：' + item.video_id + '。修改路径仍保留旧学习链接。' : '确认媒体文件，再填写需要展示的信息。没有字幕也可以导入并播放。');
  }
  function scan(message, focusId) {
    scanButton.disabled = true;
    return App.apiGet('/api/video-folders').then(function (data) {
      list.replaceChildren(); selected = null; form.hidden = true;
      status.textContent = message || '找到 ' + data.folders.length + ' 个媒体目录。';
      data.folders.forEach(function (item) {
        var button = document.createElement('button'); button.type = 'button'; button.className = 'btn';
        button.textContent = (item.video_id ? '已导入 · ' : '待导入 · ') + item.folder;
        button.addEventListener('click', function () { select(item, button); }); list.appendChild(button);
        if (focusId && item.video_id === focusId) select(item, button);
      });
      return App.apiGet('/api/videos');
    }).then(function (data) {
      if (data.issues.length) status.textContent += '\n' + data.issues.map(function (issue) { return issue.file + '：' + issue.error; }).join('\n');
      if (message) status.textContent = message + '\n' + status.textContent;
    }).catch(function (error) { status.textContent = error.message; }).finally(function () { scanButton.disabled = false; });
  }
  scanButton.addEventListener('click', function () { scan(); });
  form.addEventListener('submit', function (event) {
    event.preventDefault(); if (!selected) return;
    var metadata = {};
    Array.from(form.elements).forEach(function (element) {
      if (element.name && element.name !== 'folder') metadata[element.name] = element.value.trim();
    });
    metadata.tags = metadata.tags.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    save.disabled = true; status.textContent = '正在校验并保存…';
    App.apiPost('/api/videos', {folder:form.elements.folder.value.trim(), metadata:metadata, id:selected.video_id})
      .then(function (data) { return scan('已保存：' + data.video.title + '。视频库已自动更新。', data.video.id); })
      .catch(function (error) { status.textContent = '保存失败：' + error.message; })
      .finally(function () { save.disabled = false; });
  });
  scan();
})();
