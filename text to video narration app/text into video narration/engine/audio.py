"""Audio helpers: decoding with ffmpeg, background music, mixing."""
import os
import subprocess
import wave

import numpy as np

SR = 48000  # divisible by 24/25/30/60 fps -> exact samples per video frame

_FFMPEG = None


def ffmpeg_exe():
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    try:
        import imageio_ffmpeg
        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG = "ffmpeg"
    return _FFMPEG


def no_window():
    """Stop a console window flashing up on Windows for every ffmpeg call."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def decode(path, sr=SR):
    cmd = [ffmpeg_exe(), "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"]
    res = subprocess.run(cmd, capture_output=True, **no_window())
    if res.returncode != 0:
        raise RuntimeError(f"Could not read audio {os.path.basename(path)}: "
                           f"{res.stderr.decode(errors='ignore')[:300]}")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()


def trim_silence(samples, sr=SR, threshold=0.012, pad=0.04):
    """Cut silence at both ends. Returns (samples, seconds_cut_from_start)."""
    if len(samples) == 0:
        return samples, 0.0
    idx = np.flatnonzero(np.abs(samples) > threshold)
    if len(idx) == 0:
        return samples, 0.0
    a = max(0, idx[0] - int(pad * sr))
    b = min(len(samples), idx[-1] + int(pad * sr * 2))
    return samples[a:b], a / sr


def normalize_clip(samples, target_rms=0.11, peak=0.92):
    if len(samples) == 0 or not np.any(samples):
        return samples.astype(np.float32)
    rms = float(np.sqrt(np.mean(samples ** 2))) + 1e-9
    g = target_rms / rms
    pk = float(np.max(np.abs(samples))) * g
    if pk > peak:
        g *= peak / pk
    return (samples * g).astype(np.float32)


def frame_envelope(samples, fps, sr=SR):
    """Loudness (0..1) for every video frame, used for lip-sync."""
    hop = max(1, int(round(sr / fps)))
    n = int(np.ceil(len(samples) / hop))
    padded = np.zeros(n * hop, np.float32)
    padded[:len(samples)] = samples
    env = np.sqrt(np.mean(padded.reshape(n, hop) ** 2, axis=1))
    voiced = env[env > 0.01]
    ref = np.percentile(voiced, 90) if len(voiced) else 1.0
    env = np.clip(env / (ref + 1e-9), 0, 1)
    env = np.convolve(env, np.array([0.25, 0.5, 0.25]), mode="same")
    return env.astype(np.float32)


def write_wav(path, samples, sr=SR):
    data = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


# ------------------------------------------------------------------ music
MOODS = ["Happy", "Calm", "Funny", "Suspense", "Sad", "None"]

# bpm, chords (MIDI notes), arpeggio pattern, pluck decay
_STYLE = {
    "Happy": (104, [[60, 64, 67], [67, 71, 74], [69, 72, 76], [65, 69, 72]], [0, 1, 2, 3, 2, 1, 2, 3], 5.0),
    "Calm": (70, [[65, 69, 72, 76], [60, 64, 67, 71], [62, 65, 69, 72], [67, 71, 74, 77]], [0, 2, 1, 3, 2, 1, 3, 2], 2.5),
    "Funny": (128, [[60, 64, 67], [65, 69, 72], [67, 71, 74], [60, 64, 67]], [0, 2, 1, 2, 3, 2, 1, 2], 11.0),
    "Suspense": (78, [[57, 60, 64], [53, 57, 60], [50, 53, 57], [52, 56, 59]], [0, 1, 2, 1, 0, 1, 2, 1], 3.0),
    "Sad": (66, [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]], [0, 1, 2, 3, 2, 1, 2, 1], 2.0),
}


def _f(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_music(duration, mood, sr=SR):
    """Generate a simple royalty-free background loop (no copyright problems on TikTok)."""
    n_total = int(duration * sr)
    if mood not in _STYLE or n_total <= 0:
        return np.zeros(max(n_total, 0), np.float32)
    bpm, chords, pattern, decay = _STYLE[mood]
    beat = 60.0 / bpm
    nbar = int(4 * beat * sr)
    loop = np.zeros(nbar * len(chords), np.float32)
    t_bar = np.arange(nbar) / sr
    pad_env = np.minimum(1, t_bar / 0.3) * np.minimum(1, (4 * beat - t_bar) / 0.4)
    for ci, chord in enumerate(chords):
        off = ci * nbar
        # soft pad
        pad = sum(np.sin(2 * np.pi * _f(m) * t_bar) + 0.25 * np.sin(2 * np.pi * 2 * _f(m) * t_bar) for m in chord)
        loop[off:off + nbar] += 0.16 * pad / len(chord) * pad_env
        # bass on beats 1 and 3
        nb = int(2 * beat * sr)
        tb = np.arange(nb) / sr
        bass = np.sin(2 * np.pi * _f(chord[0] - 24) * tb) * np.exp(-tb * 2.2) * np.minimum(1, tb / 0.01)
        for b in (0, 2):
            s = off + int(b * beat * sr)
            loop[s:s + nb] += 0.30 * bass[:len(loop[s:s + nb])]
        # plucked arpeggio, eighth notes
        notes = chord + [chord[0] + 12]
        ne = int(beat / 2 * sr)
        te = np.arange(ne) / sr
        env = np.exp(-te * decay) * np.minimum(1, te / 0.006)
        for step, idx in enumerate(pattern):
            if mood == "Suspense" and step % 2:
                continue
            fr = _f(notes[idx % len(notes)] + 12)
            tone = (np.sin(2 * np.pi * fr * te) + 0.3 * np.sin(2 * np.pi * 2 * fr * te)) * env
            s = off + step * ne
            loop[s:s + ne] += 0.20 * tone[:len(loop[s:s + ne])]
    loop /= (np.max(np.abs(loop)) + 1e-9)
    music = np.tile(loop, int(np.ceil(n_total / len(loop))))[:n_total]
    return _fade(music, sr)


def _fade(music, sr, fin=1.5, fout=2.5):
    n = len(music)
    a, b = min(n, int(fin * sr)), min(n, int(fout * sr))
    if a:
        music[:a] *= np.linspace(0, 1, a)
    if b:
        music[-b:] *= np.linspace(1, 0, b)
    return music.astype(np.float32)


def load_music_file(path, duration, sr=SR):
    m = decode(path, sr)
    n_total = int(duration * sr)
    if len(m) == 0:
        return np.zeros(n_total, np.float32)
    m = m / (np.max(np.abs(m)) + 1e-9)
    m = np.tile(m, int(np.ceil(n_total / len(m))))[:n_total]
    return _fade(m, sr)


def mix(voice, music, music_volume=0.16, sr=SR):
    """Voice + music, with the music automatically ducking under speech."""
    out = voice.astype(np.float32).copy()
    if music is not None and len(music) and np.any(music):
        music = music[:len(out)]
        if len(music) < len(out):
            music = np.pad(music, (0, len(out) - len(music)))
        hop = sr // 50
        n = int(np.ceil(len(out) / hop))
        padded = np.zeros(n * hop, np.float32)
        padded[:len(out)] = out
        env = np.sqrt(np.mean(padded.reshape(n, hop) ** 2, axis=1))
        active = (env > 0.01).astype(np.float32)
        win = 20
        active = np.clip(np.convolve(active, np.ones(win) / win, mode="same") * 2, 0, 1)
        gain = np.repeat(1.0 - 0.6 * active, hop)[:len(out)]
        out += music * music_volume * gain
    peak = float(np.max(np.abs(out))) if len(out) else 0
    if peak > 0.98:
        out *= 0.98 / peak
    return out
