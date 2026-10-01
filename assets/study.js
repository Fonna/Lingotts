(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var videoSelect = $('studyVideo'), clipSelect = $('studyClip'), media = $('studyMedia');
  var dataset, clip, version = 0, mode = 'blind', loop = false, generation = 0, saving = false;
  var params = new URLSearchParams(location.search);
  function message(error) { $('studyStatus').textContent = error.message || String(error); }
  function setMode(value) {
    mode = value; $('studyTranscript').hidden = mode === 'blind';
    $('studyMode').textContent = mode === 'blind' ? '显示英文字幕' : '隐藏字幕，盲听';
    $('studyMode').setAttribute('aria-pressed', String(mode !== 'blind'));
  }
  function setLoop(value) { loop = value; $('studyLoop').textContent = '片段循环：' + (loop ? '开' : '关'); $('studyLoop').setAttribute('aria-pressed',String(loop)); }
  function seek(t) { if (clip) media.currentTime = Math.min(clip.end,Math.max(clip.start,t)); }
  function selectClip() {
    media.pause(); clip = dataset.clips.find(function (item) { return item.id === clipSelect.value; });
    $('studyWorkspace').hidden = !clip;
    if (!clip) return;
    var state = dataset.learning.segments[clip.id];
    $('clipTitle').textContent = clip.label + ' · ' + App.fmtTime(clip.start) + '–' + App.fmtTime(clip.end);
    setMode(state ? state.mode : 'blind'); setLoop(state ? state.loop : false);
    $('studySpeed').value = String(state ? state.speed : 1); media.playbackRate = Number($('studySpeed').value);
    $('studyNote').value = state ? state.note : '';
    document.querySelectorAll('.study-steps input').forEach(function (input) { input.checked = !!state && state.steps.includes(input.value); });
    $('studyTranscript').replaceChildren();
    clip.lines.forEach(function (line) {
      var button = document.createElement('button'); button.textContent = App.fmtTime(line.start) + '  ' + line.text;
      button.onclick = function () { seek(line.start); media.play().catch(message); };
      $('studyTranscript').appendChild(button);
    });
    seek(state ? state.t : clip.start);
    $('studySaveStatus').textContent = state ? '已恢复保存的位置和步骤。' : '新片段，尚未保存进度。';
    $('studySource').href = 'player.html?video=' + encodeURIComponent(dataset.video.id) + '&t=' + clip.start;
    history.replaceState(null,'','study.html?video=' + dataset.video.id + '&clip=' + clip.id);
    window.dispatchEvent(new CustomEvent('studyclip',{detail:{clip:clip,video:dataset.video,learning:dataset.learning,tasks:dataset.tasks[clip.id]}}));
  }
  function loadVideo() {
    var current = ++generation; media.pause(); $('studyWorkspace').hidden = true; clip = null;
    $('studyStatus').textContent = '正在读取片段…';
    App.apiGet('/api/study?video=' + encodeURIComponent(videoSelect.value)).then(function (data) {
      if (current !== generation) return;
      dataset = data; version = data.learning.version; clipSelect.replaceChildren();
      data.clips.forEach(function (item) {
        var option = new Option(item.label + ' · ' + App.fmtTime(item.start) + '–' + App.fmtTime(item.end),item.id); clipSelect.add(option);
      });
      var desired = params.get('clip');
      if (data.clips.some(function (item) { return item.id === desired; })) clipSelect.value = desired;
      else {
        var recent = data.clips.filter(function (item) { return data.learning.segments[item.id]; }).sort(function (a,b) { return data.learning.segments[b.id].updated_at.localeCompare(data.learning.segments[a.id].updated_at); })[0];
        if (recent) clipSelect.value = recent.id;
      }
      $('studyInfo').textContent = data.video.title + ' · ' + data.clips.length + ' 个片段';
      var stale = Object.values(data.learning.segments).filter(function (state) { return state.video === data.video.id && !data.clips.some(function (item) { return item.revision === state.revision; }); }).length;
      $('studyStatus').textContent = data.clips.length ? (stale ? stale + ' 个旧字幕版本的进度已保留，请按新片段学习。' : '选择片段后点击播放。切换字幕模式保留播放位置。') : '此视频待转录，目前可到完整播放器观看；补字幕后自动启用精听。';
      media.src = data.video.media_url;
      media.onloadedmetadata = selectClip;
      selectClip();
    }).catch(message);
  }
  $('studyMode').onclick = function () { setMode(mode === 'blind' ? 'subtitles' : 'blind'); };
  $('studyLoop').onclick = function () { setLoop(!loop); };
  $('studySpeed').onchange = function () { media.playbackRate = Number(this.value); };
  function togglePlay() {
    if (!clip) return;
    if (media.paused) { if (media.currentTime >= clip.end || media.currentTime < clip.start) seek(clip.start); media.play().catch(message); }
    else media.pause();
  }
  $('studyPlay').onclick = togglePlay;
  media.addEventListener('timeupdate',function () {
    if (!clip) return;
    // Browsers round media clocks below decimal caption endpoints.
    if (media.currentTime >= clip.end - 0.025) { if (loop) seek(clip.start); else { media.pause(); if (Math.abs(media.currentTime-clip.end)>0.025) seek(clip.end); } }
    if (media.currentTime < clip.start - 0.1) seek(clip.start);
    Array.from($('studyTranscript').children).forEach(function (row,index) { var line = clip.lines[index]; row.classList.toggle('active',media.currentTime >= line.start && media.currentTime < line.end); });
  });
  media.addEventListener('ended',function () { if (clip && loop) { seek(clip.start); media.play().catch(message); } });
  document.addEventListener('keydown',function (event) {
    if (!clip || event.ctrlKey || event.altKey || event.metaKey || event.repeat || /INPUT|TEXTAREA|SELECT|BUTTON|A/.test(event.target.tagName) || event.target.isContentEditable) return;
    var key = event.key.toLowerCase();
    if (key === ' ') togglePlay(); else if (key === 's') $('studyMode').click(); else if (key === 'l') $('studyLoop').click();
    else if (key === 'arrowleft') seek(media.currentTime - 5); else if (key === 'arrowright') seek(media.currentTime + 5); else return;
    event.preventDefault();
  });
  $('saveStudy').onclick = function () {
    if (!clip || saving) return;
    var current = generation, selected = clip.id;
    saving = true; this.disabled = true; $('studySaveStatus').textContent = '正在保存…';
    App.apiPost('/api/study/progress',{video:dataset.video.id,clip:selected,version:version,t:Math.max(clip.start,Math.min(clip.end,media.currentTime)),mode:mode,loop:loop,speed:media.playbackRate,
      steps:Array.from(document.querySelectorAll('.study-steps input:checked')).map(function (input) { return input.value; }),note:$('studyNote').value})
      .then(function (data) {
        if (current !== generation) return;
        dataset.learning = data.learning; version = data.learning.version;
        if (clip && clip.id === selected) $('studySaveStatus').textContent = '进度已保存，刷新或重新打开可续学。';
        window.dispatchEvent(new CustomEvent('studysaved',{detail:data.learning}));
      }).catch(function (error) { $('studySaveStatus').textContent = '保存失败：' + error.message; })
      .finally(function () { saving = false; $('saveStudy').disabled = false; });
  };
  window.Study = {
    current:function () { return {clip:clip,video:dataset && dataset.video,learning:dataset && dataset.learning,tasks:clip && dataset.tasks[clip.id]}; },
    seek:seek,
    update:function (data) { dataset.learning = data; version = data.version; },
    pause:function () { media.pause(); }
  };
  clipSelect.onchange = selectClip; videoSelect.onchange = loadVideo;
  App.apiGet('/api/videos').then(function (data) {
    videoSelect.replaceChildren(); data.videos.forEach(function (video) { videoSelect.add(new Option(video.title + (video.transcript_status === 'missing' ? ' · 待转录' : ''),video.id)); });
    var requested = params.get('video');
    var selected = data.videos.find(function (video) { return video.id === requested || video.aliases.includes(requested) || video.folder === requested; });
    if (selected) videoSelect.value = selected.id;
    if (data.videos.length) loadVideo(); else message('视频库暂无可用内容，请先导入视频。');
  }).catch(message);
})();
