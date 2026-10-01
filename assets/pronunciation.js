/* Shared English word pronunciation. No dictionary or AI request is required. */
(function () {
  'use strict';
  var active = null, generation = 0, pendingVoices = null;

  function englishVoice(voices) {
    var english = voices.filter(function (voice) { return /^en(?:[-_]|$)/i.test(voice.lang || ''); });
    function rank(voice) {
      return (voice.localService ? 0 : 10) + (/^en[-_]US$/i.test(voice.lang) ? 0 : 1);
    }
    return english.sort(function (a, b) { return rank(a) - rank(b); })[0] || null;
  }

  function reset(control, message) {
    control.button.textContent = '🔊 读音';
    control.button.setAttribute('aria-pressed', 'false');
    control.status.textContent = message || '';
    clearTimeout(control.timer);
    control.utterance = null;
    if (control.controller) { control.controller.abort(); control.controller = null; }
    if (control.audio) {
      control.audio.onplaying = control.audio.onended = control.audio.onerror = null;
      control.audio.pause(); control.audio.removeAttribute('src'); control.audio = null;
    }
    if (control.url) { URL.revokeObjectURL(control.url); control.url = null; }
  }

  function stop() {
    generation++;
    if (pendingVoices) pendingVoices();
    if (active) {
      var previous = active;
      active = null;
      reset(previous);
      try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (_) {}
    }
  }

  function waitForVoice(synth) {
    var voice = englishVoice(synth.getVoices());
    if (voice) return Promise.resolve(voice);
    return new Promise(function (resolve) {
      var timer, settled = false;
      function finish(voice) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        synth.removeEventListener('voiceschanged', changed);
        if (pendingVoices === cancelWait) pendingVoices = null;
        resolve(voice);
      }
      function changed() {
        var voice = englishVoice(synth.getVoices());
        if (voice) finish(voice);
      }
      function cancelWait() { finish(null); }
      pendingVoices = cancelWait;
      synth.addEventListener('voiceschanged', changed);
      timer = setTimeout(function () { finish(englishVoice(synth.getVoices())); }, 1800);
      changed();
    });
  }

  function speak(word, control) {
    if (active === control) { stop(); return; }
    stop();
    if (typeof word !== 'string' || !/^[A-Za-z]+(?:['’-][A-Za-z]+)*$/.test(word) || word.length > 80) {
      reset(control, '这个词暂时无法朗读');
      return;
    }
    var synth = window.speechSynthesis;
    var request = generation;
    active = control;
    control.button.textContent = '■ 停止';
    control.button.setAttribute('aria-pressed', 'true');
    control.status.textContent = '准备读音…';
    function finish(message) {
      if (request !== generation || active !== control) return;
      active = null;
      reset(control, message);
    }
    function pauseMedia() {
      document.querySelectorAll('video, audio').forEach(function (media) {
        if (!media.paused) media.pause();
      });
    }
    var fallbackStarted = false;
    function fallback() {
      if (request !== generation || active !== control || fallbackStarted) return;
      fallbackStarted = true;
      clearTimeout(control.timer);
      if (control.utterance) {
        control.utterance.onstart = control.utterance.onend = control.utterance.onerror = null;
        try { synth.cancel(); } catch (_) {}
      }
      control.status.textContent = '准备读音…';
      control.controller = new AbortController();
      control.timer = setTimeout(function () { finish('读音准备超时，请重试'); }, 15000);
      fetch('/api/pronunciation?word=' + encodeURIComponent(word), {signal:control.controller.signal})
        .then(function (response) {
          if (!response.ok) return response.json().then(function (data) {
            throw new Error(data.error || '本地朗读不可用，请重试');
          });
          return response.blob();
        }).then(function (blob) {
          if (request !== generation || active !== control) return;
          control.url = URL.createObjectURL(blob);
          var audio = new Audio(control.url); control.audio = audio;
          audio.onplaying = function () {
            if (request === generation && active === control) control.status.textContent = '正在朗读';
          };
          audio.onended = function () { finish(''); };
          audio.onerror = function () { finish('读音播放失败，请检查声音后重试'); };
          pauseMedia();
          return audio.play();
        }).catch(function (error) {
          finish(error.name === 'NotAllowedError' ? '浏览器阻止了播放，请再次点击读音' :
            (error.message || '本地朗读不可用，请重试'));
        });
    }
    if (!synth || !window.SpeechSynthesisUtterance) { fallback(); return; }
    Promise.resolve().then(function () {
      if (request !== generation || active !== control) return null;
      return waitForVoice(synth);
    }).then(function (voice) {
      if (request !== generation || active !== control) return;
      if (!voice) { fallback(); return; }
      var utterance = new window.SpeechSynthesisUtterance(word);
      control.utterance = utterance; // Keep the utterance alive until playback completes.
      utterance.voice = voice;
      utterance.lang = voice.lang;
      utterance.rate = 0.85;
      utterance.onstart = function () {
        if (!fallbackStarted && request === generation && active === control) control.status.textContent = '正在朗读';
      };
      utterance.onend = function () { if (!fallbackStarted) finish(''); };
      utterance.onerror = fallback;
      pauseMedia();
      control.timer = setTimeout(function () {
        if (request !== generation || active !== control) return;
        fallback();
      }, 15000);
      synth.speak(utterance);
    }).catch(fallback);
  }

  function addPronunciation(container, word) {
    var group = document.createElement('span'); group.className = 'pronunciation-control';
    var button = document.createElement('button');
    button.type = 'button'; button.className = 'pronunciation-btn';
    button.setAttribute('aria-label', '朗读单词 ' + word);
    button.setAttribute('aria-pressed', 'false'); button.textContent = '🔊 读音';
    var status = document.createElement('span'); status.className = 'pronunciation-status';
    status.setAttribute('role', 'status');
    var control = {button:button, status:status, timer:null};
    button.addEventListener('click', function () { speak(word, control); });
    group.append(button, status); container.appendChild(group);
    return control;
  }

  App.addPronunciation = addPronunciation;
  App.stopPronunciation = stop;
  window.addEventListener('pagehide', stop);
})();
