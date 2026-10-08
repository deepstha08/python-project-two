"""One function that goes from story text to a finished MP4."""
import os
import time

from . import ai_writer, renderer, voices
from . import script_parser as sp
from .characters import Character

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "outputs")
CACHE_DIR = os.path.join(ROOT, ".cache", "tts")

DURATIONS = {"1 min": 1, "3 min": 3, "5 min": 5, "8 min": 8, "10 min": 10, "15 min": 15}
QUALITIES = {
    "1080x1920 HD (TikTok, best)": (1080, 1920, 30, "fast", 19),
    "720x1280 (faster render)": (720, 1280, 30, "veryfast", 21),
}
WORDS_PER_SEC = 2.7


def generate(story, minutes=1, title="", quality=None, music_mood="Happy", music_file=None,
             narrator_voice=voices.NARRATOR_DEFAULT, end_text="Follow for more stories!",
             characters=None, ai=None, use_ai=True, log=print, progress=lambda f, m="": None):
    """
    characters: list of dicts {name, look, voice, photo} (all optional)
    ai: dict {provider, api_key, model, base_url}
    Returns (video_path, script_text, report).
    """
    t0 = time.time()
    characters = [c for c in (characters or []) if c.get("name", "").strip()]
    ai = ai or {}
    provider = ai.get("provider") or ai_writer.PROVIDER_NONE
    target = minutes * 60
    notes = []

    text = (story or "").strip()
    if not text:
        raise ValueError("Please type a story (or just an idea if you use the AI writer).")
    used_ai = False
    if use_ai and provider != ai_writer.PROVIDER_NONE and not sp.looks_like_script(text):
        progress(0.01, "AI is writing the script…")
        cast = [(c["name"].strip(), c.get("look", "Auto")) for c in characters]
        text = ai_writer.write_script(text, minutes, provider, ai.get("api_key", ""), ai.get("model", ""),
                                      ai.get("base_url", ""), cast=cast, log=log)
        used_ai = True

    script = sp.parse(text)
    if title.strip():
        script.title = title.strip()
    names = script.characters()
    log(f"Found {len(script.scenes)} scene(s), {len(script.all_lines())} lines, "
        f"characters: {', '.join(names) if names else 'narrator only'}")

    # ---- cast
    overrides = {c["name"].strip().lower(): c for c in characters}
    for c in characters:
        if c["name"].strip().lower() not in [n.lower() for n in names]:
            notes.append(f"Character '{c['name']}' isn't in the story, so their settings weren't used.")
    assignment = voices.assign_voices(names, overrides, narrator_voice)
    cast = {}
    for n in names:
        ov = overrides.get(n.lower(), {})
        label, kind = assignment[n]
        photo = ov.get("photo") or None
        try:
            cast[n] = Character(n, kind, photo)
        except Exception as e:
            notes.append(f"Couldn't use the photo for {n} ({e}); using a cartoon face.")
            cast[n] = Character(n, kind, None)
    narrator_onscreen = not names
    nar_kind = (voices.VOICES.get(narrator_voice) or voices.VOICES[voices.NARRATOR_DEFAULT])[1]
    if narrator_onscreen:
        cast[sp.NARRATOR] = Character("Storyteller", nar_kind)

    # ---- speed: squeeze long text a little so it fits the chosen length
    lines = script.all_lines()
    est = script.word_count() / WORDS_PER_SEC + len(lines) * renderer.BASE_GAP + len(script.scenes) * 1.1 + 3
    global_rate = 0
    if est > target * 1.05:
        global_rate = int(min(20, (est / target - 1) * 100))
        if global_rate:
            log(f"Text is a bit long for {minutes} min – speaking {global_rate}% faster.")

    # ---- voices
    jobs = []
    for ln in lines:
        label = narrator_voice if ln.is_narration else assignment[ln.speaker][0]
        jobs.append((ln.text, label, ln.emotion))
    clips = voices.synthesize(jobs, CACHE_DIR, global_rate, log=log,
                              progress=lambda f: progress(0.05 + 0.15 * f, "Recording voices…"))

    # ---- fit to target length with natural pauses
    _, _, base_total = renderer.plan_timeline(script, clips, 0.0, end_card=bool(end_text))
    extra_gap = 0.0
    if base_total < target:
        extra_gap = min(0.8, (target - base_total) / max(1, len(lines)))
    final_est = base_total + extra_gap * len(lines)
    if final_est < target * 0.85:
        if used_ai:
            notes.append(f"The AI script came out a little short ({final_est / 60:.1f} min).")
        else:
            notes.append(f"Your text only has enough words for about {final_est / 60:.1f} min "
                         f"(a {minutes} min video needs ~{int(minutes * 150)} words). "
                         f"Write more, or turn on the AI writer to stretch it to {minutes} min.")
    elif final_est > target * 1.2:
        notes.append(f"Your text is longer than {minutes} min, so the video is {final_est / 60:.1f} min. "
                     f"Pick a longer duration or shorten the text.")

    # ---- render
    width, height, fps, preset, crf = QUALITIES.get(quality) or list(QUALITIES.values())[0]
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe = "".join(ch for ch in (script.title or "story") if ch.isalnum() or ch in " -_").strip()[:40] or "story"
    out_path = os.path.join(OUTPUT_DIR, f"{safe.replace(' ', '_')}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
    total = renderer.render(
        script, cast, clips, out_path, width, height, fps, title=script.title, end_text=end_text.strip(),
        music_mood=music_mood, music_file=music_file, extra_gap=extra_gap, preset=preset, crf=crf,
        narrator_onscreen=narrator_onscreen, log=log,
        progress=lambda f, m="": progress(0.2 + 0.8 * f, m))

    voice_list = "\n".join(f"  • {n}: {assignment[n][0]}" for n in names)
    report = (f"✅ Done in {(time.time() - t0) / 60:.1f} min → {os.path.basename(out_path)}\n"
              f"Length: {int(total // 60)}:{int(total % 60):02d}  |  {width}x{height} @ {fps}fps\n"
              f"Voices:\n{voice_list or '  • narrator only'}\n  • Narrator: {narrator_voice}")
    if notes:
        report += "\n\nNotes:\n" + "\n".join("⚠ " + n for n in notes)
    return out_path, script.to_text(), report
