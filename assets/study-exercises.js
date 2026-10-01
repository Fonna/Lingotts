(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); }, pending = false, currentId = null;
  var labels = {cloze:'补全',dictation:'听写',understanding:'内容理解',summary:'英文概述',choice:'理解选择题',context:'语境运用'};
  function verdict(item) { return item.correct === null ? ('自评：' + (item.self_rating === 'clear' ? '表达清楚' : '还需练习')) : item.correct ? '答案吻合原文' : '需要回听并重做'; }
  function feedback(item,container) {
    container.replaceChildren();
    var heading = document.createElement('p'); heading.textContent = verdict(item) + '。' + item.explanation; container.appendChild(heading);
    var reference = document.createElement('p'); reference.textContent = '原文参考：' + item.reference; container.appendChild(reference);
    if (item.word_diff) {
      var comparison = document.createElement('div'); comparison.className = 'word-comparison';
      var title = document.createElement('p'); title.textContent = '逐词对照（忽略大小写与标点）'; comparison.appendChild(title);
      var list = document.createElement('ol'); list.className = 'word-diff';
      var names = {equal:'吻合', missing:'漏写', extra:'多写', replace:'不一致'};
      item.word_diff.forEach(function (part) {
        var token = document.createElement('li'); token.className = 'word-diff-' + part.kind;
        var label = document.createElement('span'); label.className = 'word-diff-label'; label.textContent = names[part.kind]; token.appendChild(label);
        var text = document.createElement('span');
        text.textContent = part.kind === 'replace' ? '你写：' + part.actual.join(' ') + ' → 原文：' + part.expected.join(' ') : (part.expected.length ? part.expected : part.actual).join(' ');
        token.appendChild(text); list.appendChild(token);
      });
      comparison.appendChild(list); container.appendChild(comparison);
    }
    item.evidence.forEach(function (line) {
      var button = document.createElement('button'); button.className = 'btn'; button.textContent = '回听 ' + App.fmtTime(line.start) + ' · ' + line.text;
      button.onclick = function () { Study.seek(line.start); document.getElementById('studyMedia').play().catch(function (error) { $('exerciseStatus').textContent = error.message; }); };
      container.appendChild(button);
    });
  }
  function renderHistory() {
    var data = Study.current(), box = $('attemptHistory'); box.replaceChildren();
    if (!data.clip) return;
    var records = data.learning.attempts.filter(function (item) { return item.clip === data.clip.id; });
    if (!records.length) { box.textContent = '暂无尝试。回答提交后保存在本地，可随时重做。'; return; }
    records.slice().reverse().slice(0,20).forEach(function (item) {
      var row = document.createElement('details'), summary = document.createElement('summary'), body = document.createElement('div');
      summary.textContent = labels[item.kind] + ' · ' + verdict(item) + ' · ' + new Date(item.created_at).toLocaleString('zh-CN');
      row.appendChild(summary); var answer = document.createElement('p'); answer.textContent = '我的回答：' + item.answer; row.appendChild(answer);
      feedback(item,body); row.appendChild(body); box.appendChild(row);
    });
  }
  function render() {
    var data = Study.current(); currentId = data.clip && data.clip.id;
    $('studyExercises').hidden = !data.clip;
    if (!data.clip) return;
    $('exerciseList').replaceChildren(); $('exerciseStatus').textContent = '';
    data.tasks.forEach(function (task) {
      var row = document.createElement('article'); row.className = 'exercise'; row.dataset.task = task.id;
      var heading = document.createElement('h3'); heading.textContent = labels[task.kind]; row.appendChild(heading);
      var prompt = document.createElement('p'); prompt.textContent = task.prompt; row.appendChild(prompt);
      var play = document.createElement('button'); play.className = 'btn'; play.textContent = '回听题目原句 ' + App.fmtTime(task.listen_at);
      play.onclick = function () { Study.seek(task.listen_at); $('studyMedia').play().catch(function (error) { $('exerciseStatus').textContent = error.message; }); }; row.appendChild(play);
      var answer, self = null;
      if (task.kind === 'choice') {
        answer = document.createElement('select'); answer.className = 'exercise-answer'; answer.add(new Option('请选择',''));
        task.choices.forEach(function (choice,index) { answer.add(new Option(choice,String(index))); });
      } else { answer = document.createElement('textarea'); answer.rows = task.kind === 'cloze' ? 1 : 3; answer.maxLength = 4000; answer.className = 'exercise-answer'; }
      answer.setAttribute('aria-label',labels[task.kind] + '回答'); row.appendChild(answer);
      if (task.kind === 'summary' || task.kind === 'understanding') {
        self = document.createElement('select'); self.setAttribute('aria-label',labels[task.kind] + '自评');
        self.add(new Option('对照原文后自评','')); self.add(new Option('还需练习','needs_work')); self.add(new Option('表达清楚且有依据','clear')); row.appendChild(self);
      }
      var submit = document.createElement('button'); submit.className = 'btn btn-primary'; submit.textContent = '提交并查看依据'; row.appendChild(submit);
      var result = document.createElement('div'); result.className = 'exercise-feedback'; result.setAttribute('role','status'); row.appendChild(result);
      var requestId = null, payload = null;
      submit.onclick = function () {
        if (pending) return;
        if (!answer.value.trim() || (self && !self.value)) { result.textContent = '请填写回答，并完成主观题自评。'; return; }
        var state = Study.current(), key = JSON.stringify([answer.value,self && self.value]);
        if (payload !== key) { requestId = crypto.randomUUID().replace(/-/g,''); payload = key; }
        var selected = currentId; pending = true; submit.disabled = true; result.textContent = '正在核对并保存…';
        App.apiPost('/api/study/attempt',{video:state.video.id,clip:state.clip.id,task:task.id,version:state.learning.version,answer:answer.value,self_rating:self ? self.value : null,request_id:requestId})
          .then(function (data) {
            if (currentId !== selected) return;
            Study.update(data.learning); feedback(data.attempt,result); renderHistory();
            submit.textContent = '重做并记录新尝试'; requestId = null; payload = null;
          }).catch(function (error) { result.textContent = '保存失败：' + error.message; })
          .finally(function () { pending = false; submit.disabled = false; });
      };
      $('exerciseList').appendChild(row);
      if (new URLSearchParams(location.search).get('task') === task.id) {
        row.style.borderColor = 'var(--accent)';
        setTimeout(function () { if (row.isConnected) row.scrollIntoView({block:'start'}); },0);
      }
    });
    renderHistory();
  }
  window.addEventListener('studyclip',render); window.addEventListener('studysaved',renderHistory);
})();
