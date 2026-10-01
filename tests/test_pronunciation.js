const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../assets/pronunciation.js'), 'utf8');
const english = { lang: 'en-US', localService: true };
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup(options = {}) {
  const timers = new Map(), listeners = new Set(), pageEvents = {};
  const audioPlayed = [], requests = [], revoked = [];
  let nextTimer = 0;
  const media = { paused: false, pause() { this.paused = true; } };
  function element() {
    return {
      attrs: {}, children: [], handlers: {}, textContent: '',
      setAttribute(key, value) { this.attrs[key] = value; },
      addEventListener(key, handler) { this.handlers[key] = handler; },
      append(...children) { this.children.push(...children); },
      appendChild(child) { this.children.push(child); },
      click() { this.handlers.click(); }
    };
  }
  const synth = {
    voices: options.voices || [english], spoken: [], cancels: 0,
    getVoices() { return this.voices; },
    addEventListener(name, handler) { listeners.add(handler); },
    removeEventListener(name, handler) { listeners.delete(handler); },
    cancel() { this.cancels++; if (options.cancelThrows) throw Error('cancel'); },
    speak(utterance) {
      if (options.speakThrows) throw Error('speak');
      this.spoken.push(utterance);
    },
    voicesChanged(voices) { this.voices = voices; [...listeners].forEach(handler => handler()); }
  };
  const window = {
    speechSynthesis: options.unsupported ? undefined : synth,
    SpeechSynthesisUtterance: function (text) { this.text = text; },
    addEventListener(name, handler) { pageEvents[name] = handler; }
  };
  const context = {
    App: {}, window,
    AbortController,
    URL: { createObjectURL: () => 'blob:word', revokeObjectURL: url => revoked.push(url) },
    Audio: function (url) {
      this.src = url;
      this.pause = () => { this.paused = true; };
      this.removeAttribute = () => { this.src = ''; };
      this.play = () => {
        if (options.playRejects) return Promise.reject(Object.assign(Error('blocked'), {name:'NotAllowedError'}));
        audioPlayed.push(this); this.onplaying(); return Promise.resolve();
      };
    },
    fetch(url, settings) {
      requests.push({url, settings});
      if (options.fetchPending) return new Promise(resolve => { requests.at(-1).resolve = resolve; });
      return Promise.resolve({ok:!!options.audioOK, json: async () => ({error:'本地朗读不可用，请检查英语语音包'}),
        blob: async () => ({})});
    },
    document: { createElement: element, querySelectorAll: () => [media] },
    setTimeout(handler, delay) { const id = ++nextTimer; timers.set(id, { handler, delay }); return id; },
    clearTimeout(id) { timers.delete(id); }
  };
  vm.runInNewContext(source, context);
  return {
    synth, media, timers, listeners, pageEvents, audioPlayed, requests, revoked, app: context.App,
    control(word = 'hello') { return context.App.addPronunciation(element(), word); },
    timeout(delay) {
      [...timers].filter(([, timer]) => timer.delay === delay).forEach(([id, timer]) => {
        timers.delete(id); timer.handler();
      });
    }
  };
}

test('click speaks the requested word with local English voice and pauses media', async () => {
  const s = setup({ voices: [{ lang: 'zh-CN', localService: true },
    { lang: 'en-US', localService: false }, { lang: 'en-GB', localService: true }] });
  const c = s.control('comfortable');
  assert.equal(s.synth.spoken.length, 0);
  assert.equal(c.button.type, 'button');
  assert.equal(c.button.attrs['aria-label'], '朗读单词 comfortable');
  c.button.click(); await flush();
  const u = s.synth.spoken[0];
  assert.equal(u.text, 'comfortable');
  assert.equal(u.lang, 'en-GB');
  assert.equal(u.rate, 0.85);
  assert.equal(s.media.paused, true);
  u.onstart(); assert.equal(c.status.textContent, '正在朗读');
  u.onend(); assert.equal(c.button.attrs['aria-pressed'], 'false');
  assert.equal(s.timers.size, 0);
});

test('prefers US English among equally local voices', async () => {
  const s = setup({ voices: [{ lang: 'en-GB', localService: true }, english] });
  s.control().button.click(); await flush();
  assert.equal(s.synth.spoken[0].voice, english);
});

test('repeated click stops and ignores late callbacks', async () => {
  const s = setup(), c = s.control();
  c.button.click(); await flush(); const u = s.synth.spoken[0];
  c.button.click(); u.onstart(); u.onerror();
  assert.equal(s.synth.cancels, 1);
  assert.equal(c.status.textContent, '');
  assert.equal(c.button.attrs['aria-pressed'], 'false');
});

test('switching words cancels old speech and old completion cannot reset new speech', async () => {
  const s = setup(), a = s.control('first'), b = s.control('second');
  a.button.click(); await flush(); const old = s.synth.spoken[0];
  b.button.click(); await flush(); old.onend();
  assert.equal(s.synth.cancels, 1);
  assert.equal(s.synth.spoken[1].text, 'second');
  assert.equal(b.button.attrs['aria-pressed'], 'true');
  assert.equal(a.button.attrs['aria-pressed'], 'false');
});

test('rapid clicks before microtasks only request the final word', async () => {
  const s = setup({ voices: [] }), a = s.control('first'), b = s.control('second');
  a.button.click(); b.button.click(); await flush();
  assert.equal(s.listeners.size, 1);
  s.synth.voicesChanged([english]); await flush();
  assert.deepEqual(s.synth.spoken.map(u => u.text), ['second']);
  assert.equal(s.listeners.size, 0);
});

test('switching while voices load removes previous listener and waits for new request', async () => {
  const s = setup({ voices: [] }), a = s.control('first'), b = s.control('second');
  a.button.click(); await flush(); b.button.click(); await flush();
  assert.equal(s.listeners.size, 1);
  s.synth.voicesChanged([english]); await flush();
  assert.deepEqual(s.synth.spoken.map(u => u.text), ['second']);
  assert.equal(s.timers.size, 1); // Only the active speech watchdog remains.
});

test('stopping a pending request never starts speech after voices appear', async () => {
  const s = setup({ voices: [] }), c = s.control();
  c.button.click(); await flush(); s.app.stopPronunciation();
  s.synth.voicesChanged([english]); await flush();
  assert.equal(s.synth.spoken.length, 0);
  assert.equal(s.listeners.size, 0);
  assert.equal(s.timers.size, 0);
});

test('missing English voice shows an actionable message and allows retry', async () => {
  const s = setup({ voices: [{ lang: 'zh-CN', localService: true }] }), c = s.control();
  c.button.click(); await flush(); s.timeout(1800); await flush();
  assert.match(c.status.textContent, /英语语音/);
  assert.equal(s.synth.spoken.length, 0);
  assert.equal(c.button.attrs['aria-pressed'], 'false');
  s.synth.voicesChanged([english]); c.button.click(); await flush();
  assert.equal(s.synth.spoken.length, 1);
});

test('unsupported browser tries fallback and shows failure message', async () => {
  const s = setup({ unsupported: true }), c = s.control();
  c.button.click(); await flush();
  assert.match(c.status.textContent, /本地朗读不可用/);
  assert.equal(s.requests.length, 1);
  assert.equal(c.button.attrs['aria-pressed'], 'false');
});

test('invalid word cannot send markup or sentences to speech engine', async () => {
  const s = setup();
  for (const word of ['<script>', 'hello world', '', 'a'.repeat(81)]) {
    const c = s.control(word); c.button.click();
    assert.match(c.status.textContent, /无法朗读/);
  }
  await flush(); assert.equal(s.synth.spoken.length, 0);
});

test('apostrophes and hyphens remain pronounceable', async () => {
  const s = setup();
  for (const word of ["don't", 'self-esteem', 'it’s']) {
    s.control(word).button.click(); await flush();
  }
  assert.deepEqual(s.synth.spoken.map(u => u.text), ["don't", 'self-esteem', 'it’s']);
});

test('speech errors and thrown errors restore retry button', async () => {
  for (const speakThrows of [false, true]) {
    const s = setup({ speakThrows }), c = s.control();
    c.button.click(); await flush();
    if (!speakThrows) s.synth.spoken[0].onerror();
    await flush();
    assert.match(c.status.textContent, /本地朗读不可用/);
    assert.equal(c.button.attrs['aria-pressed'], 'false');
    assert.equal(s.timers.size, 0);
  }
});

test('silent engine times out and tries fallback', async () => {
  const s = setup(), c = s.control();
  c.button.click(); await flush(); s.timeout(15000);
  await flush();
  assert.match(c.status.textContent, /本地朗读不可用/);
  assert.equal(s.synth.cancels, 1);
  assert.equal(c.button.attrs['aria-pressed'], 'false');
});

test('absent browser voice plays server audio and releases blob on completion', async () => {
  const s = setup({voices:[], audioOK:true}), c = s.control('comfy');
  c.button.click(); await flush(); s.timeout(1800); await flush();
  assert.equal(s.requests[0].url, '/api/pronunciation?word=comfy');
  assert.equal(s.audioPlayed.length, 1);
  assert.equal(c.status.textContent, '正在朗读');
  const audio = s.audioPlayed[0]; audio.onended();
  assert.equal(audio.paused, true);
  assert.deepEqual(s.revoked, ['blob:word']);
  assert.equal(c.status.textContent, '');
});

test('cancelling server request prevents late audio playback', async () => {
  const s = setup({unsupported:true, fetchPending:true}), c = s.control();
  c.button.click(); s.app.stopPronunciation();
  assert.equal(s.requests[0].settings.signal.aborted, true);
  s.requests[0].resolve({ok:true, blob:async () => ({})}); await flush();
  assert.equal(s.audioPlayed.length, 0);
  assert.equal(s.timers.size, 0);
});

test('stop cancels fallback audio and clears resources', async () => {
  const s = setup({unsupported:true, audioOK:true}), c = s.control();
  c.button.click(); await flush(); c.button.click();
  assert.equal(s.audioPlayed[0].paused, true);
  assert.equal(s.audioPlayed[0].src, '');
  assert.deepEqual(s.revoked, ['blob:word']);
  assert.equal(c.button.attrs['aria-pressed'], 'false');
});

test('audio autoplay rejection gives a retry instruction', async () => {
  const s = setup({unsupported:true, audioOK:true, playRejects:true}), c = s.control();
  c.button.click(); await flush();
  assert.match(c.status.textContent, /再次点击读音/);
  assert.equal(c.button.attrs['aria-pressed'], 'false');
  assert.equal(s.timers.size, 0);
});

test('pending fallback times out, aborts and allows retry', async () => {
  const s = setup({unsupported:true, fetchPending:true}), c = s.control();
  c.button.click(); s.timeout(15000);
  assert.match(c.status.textContent, /超时/);
  assert.equal(s.requests[0].settings.signal.aborted, true);
  c.button.click(); assert.equal(s.requests.length, 2);
});

test('page exit cancels speech even when cancellation throws', async () => {
  const s = setup({ cancelThrows: true }), c = s.control();
  c.button.click(); await flush(); s.pageEvents.pagehide();
  assert.equal(c.button.attrs['aria-pressed'], 'false');
  assert.equal(s.timers.size, 0);
});
