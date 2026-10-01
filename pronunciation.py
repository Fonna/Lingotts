"""Local Windows speech fallback for browsers without English voices."""
import base64
import functools
import os
import re
import subprocess
import threading


class PronunciationError(Exception):
    pass


_LOCK = threading.Lock()
_SCRIPT = r"""$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$stream = [System.IO.MemoryStream]::new()
try {
    $voice = $speaker.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'en'
    } | Sort-Object { if ($_.VoiceInfo.Culture.Name -eq 'en-US') { 0 } else { 1 } } | Select-Object -First 1
    if (-not $voice) { throw 'No English voice installed' }
    $speaker.SelectVoice($voice.VoiceInfo.Name)
    $speaker.Rate = -2
    $speaker.SetOutputToWaveStream($stream)
    $speaker.Speak($env:TEDLIB_PRONUNCIATION_WORD)
    $speaker.SetOutputToNull()
    [Console]::Out.Write([Convert]::ToBase64String($stream.ToArray()))
} finally {
    $speaker.Dispose()
    $stream.Dispose()
}
"""


@functools.lru_cache(maxsize=128)
def _synthesize(word):
    if os.name != 'nt':
        raise PronunciationError('本地备用朗读仅支持 Windows，请使用具有英语语音的浏览器')
    environment = dict(os.environ, TEDLIB_PRONUNCIATION_WORD=word)
    powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                              'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        result = subprocess.run(
            [powershell, '-NoProfile', '-NonInteractive', '-Command', _SCRIPT],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        audio = base64.b64decode(result.stdout, validate=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise PronunciationError('本地朗读不可用，请检查 Windows 英语语音包后重试') from exc
    if not 44 < len(audio) <= 2 * 1024 * 1024 or audio[:4] != b'RIFF' or audio[8:12] != b'WAVE':
        raise PronunciationError('本地语音生成失败，请重试')
    return audio


def speech_audio(word):
    if not isinstance(word, str):
        raise ValueError('invalid word')
    word = word.strip().lower().replace('’', "'")
    if len(word) > 80 or not re.fullmatch(r"[a-z]+(?:['-][a-z]+)*", word):
        raise ValueError('invalid word')
    # Bound simultaneous native processes; failed requests are never cached.
    if not _LOCK.acquire(timeout=0.2):
        raise PronunciationError('正在准备其他单词的读音，请稍后重试')
    try:
        return _synthesize(word)
    finally:
        _LOCK.release()
