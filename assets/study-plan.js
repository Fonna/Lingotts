(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); }, plan, version, busy = false, day, offset, generation = 0;
  function calendar() {
    var now = new Date(); day = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0'); offset = -now.getTimezoneOffset();
  }
  function render(data) {
    plan = data.plan; version = data.version; $('planQuota').value = String(plan.quota);
    $('planProgress').textContent = plan.day + ' · 今日已评分 ' + plan.counts.words + ' 个词、保存完成 ' + plan.counts.clips + ' 个精听片段、提交 ' + plan.counts.attempts + ' 次练习。';
    var box = $('planItems'); box.replaceChildren();
    if (!plan.items.length) { var empty=document.createElement('p'); empty.textContent = plan.fallback; box.appendChild(empty); }
    plan.items.forEach(function (item) {
      var row = document.createElement('article'), title = document.createElement('h3'), reason = document.createElement('p'), link = document.createElement('a'), skip = document.createElement('button');
      row.className = 'plan-item'; title.textContent = item.title; reason.textContent = '为什么：' + item.reason + '（约 ' + item.minutes + ' 分钟）';
      link.className = 'btn btn-primary'; link.href = item.href; link.textContent = '开始'; skip.className = 'btn'; skip.textContent = '今天跳过'; skip.onclick = function () { save(plan.quota,plan.skipped_ids.concat(item.id)); };
      row.append(title,reason,link,skip); box.appendChild(row);
    });
    $('planStatus').textContent = '当前显示 ' + plan.items.length + ' 项建议；共 ' + plan.remaining + ' 项可做，今天跳过 ' + plan.skipped_ids.length + ' 项。完成后刷新会按新记录调整。';
    $('restoreSkipped').disabled = !plan.skipped_ids.length;
  }
  function load() {
    if (busy) return; calendar(); var current = ++generation; $('planStatus').textContent = '正在读取今日建议…';
    App.apiGet('/api/study/plan?day=' + day + '&offset=' + offset).then(function (data) { if (current === generation) render(data); }).catch(function (error) { if (current === generation) $('planStatus').textContent = error.message; });
  }
  function save(quota,skipped) {
    if (!plan || busy) return;
    // A page left open across midnight must reload the new day before changing it.
    calendar(); if (day !== plan.day) { load(); return; }
    busy = true; ++generation; $('planStatus').textContent = '正在保存计划设置…';
    App.apiPost('/api/study/plan',{day:day,offset:offset,version:version,quota:quota,skipped:skipped}).then(render)
      .catch(function (error) { $('planStatus').textContent = '保存失败：' + error.message; })
      .finally(function () { busy = false; });
  }
  $('planQuota').onchange = function () { if (plan) save(Number(this.value),plan.skipped_ids); };
  $('refreshPlan').onclick = load; $('restoreSkipped').onclick = function () { if (plan) save(plan.quota,[]); };
  window.addEventListener('focus',load); document.addEventListener('visibilitychange',function () { if (!document.hidden) load(); });
  load();
})();
