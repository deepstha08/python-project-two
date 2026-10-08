"""Story Video Studio – type a story, get an animated TikTok video.

Start it with start_windows.bat (Windows) or start_mac_linux.sh (Mac/Linux),
or run:  python app.py   – it opens in your web browser.
"""
import inspect
import os
import sys
import traceback

import gradio as gr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import ai_writer, pipeline, voices  # noqa: E402
from engine.audio import MOODS  # noqa: E402

N_CHARS = 4

EXAMPLE_IDEA = """A shy girl named Mia finds a tiny dragon egg in the park. Her best friend Leo thinks it's \
just a rock until it starts talking. They have to hide it from Mia's grumpy Grandpa Joe, but the baby dragon \
has other plans."""

EXAMPLE_SCRIPT = """TITLE: The Talking Dragon Egg
[SCENE: park, day]
Narrator: One sunny morning, Mia found something strange under a tree.
Mia (surprised): Leo, come here! Is this... an egg?
Leo (happy): That's the biggest egg I've ever seen!
Mia: It's warm. And I think it just moved.
Leo (scared): Eggs don't move, Mia. Eggs definitely do not move.
[SCENE: kitchen, night]
Narrator: That night, they hid the egg in Mia's kitchen.
Grandpa Joe (angry): Why is there a giant egg on my table?
Mia (sad): Please don't be mad, Grandpa. We couldn't leave it alone.
Grandpa Joe (surprised): Wait... did it just say hello?
Leo (excited): It's hatching! It's really hatching!
"""

HELP = """
**Two ways to write:**
1. **Just an idea or a story** → turn on the *AI writer* below and it writes a full script with dialogue at the length you pick (1–15 min).
2. **Your own script** (no AI needed) → one line per speaker:
```
TITLE: My Story
[SCENE: park, day]
Narrator: One morning...
Mia (happy): Hi Leo!
Leo (surprised): Whoa, what is that?
```
Places: room, kitchen, park, city, school, office, beach, forest, space, cafe · Times: day, sunset, night ·
Emotions: happy, excited, sad, angry, surprised, scared
"""

CSS = """
#gen-btn {font-size: 1.2em; min-height: 56px}
.char-row {align-items: center}
footer {display: none !important}
"""


def _on_provider(provider):
    model, url = ai_writer.DEFAULTS.get(provider, ("", ""))
    show = provider != ai_writer.PROVIDER_NONE
    need_key = provider in (ai_writer.PROVIDER_CLAUDE, ai_writer.PROVIDER_OPENAI, ai_writer.PROVIDER_OTHER)
    return gr.update(value=model, visible=show), gr.update(value=url, visible=show), gr.update(visible=need_key)


def _chars_from(fields):
    chars = []
    for i in range(N_CHARS):
        name, look, voice, photo = fields[i * 4:(i + 1) * 4]
        if name and name.strip():
            chars.append({"name": name.strip(), "look": look or "Auto", "voice": voice or "Auto", "photo": photo})
    return chars


def write_with_ai(story, duration, provider, key, model, url, *char_fields, progress=gr.Progress()):
    if provider == ai_writer.PROVIDER_NONE:
        raise gr.Error("Choose an AI provider first (in the 'AI writer' box).")
    if not (story or "").strip():
        raise gr.Error("Type a short idea first.")
    chars = _chars_from(char_fields)
    progress(0.1, desc="AI is writing…")
    try:
        text = ai_writer.write_script(story, pipeline.DURATIONS[duration], provider, key, model, url,
                                      cast=[(c["name"], c["look"]) for c in chars], log=print)
    except Exception as e:
        raise gr.Error(str(e))
    return text


def make_video(story, title, duration, quality, music, music_file, narrator, end_text,
               provider, key, model, url, *char_fields, progress=gr.Progress()):
    logs = []

    def log(msg):
        print(msg)
        logs.append(msg)

    try:
        path, script_text, report = pipeline.generate(
            story=story, minutes=pipeline.DURATIONS[duration], title=title or "", quality=quality,
            music_mood=music, music_file=music_file, narrator_voice=narrator, end_text=end_text or "",
            characters=_chars_from(char_fields),
            ai={"provider": provider, "api_key": key, "model": model, "base_url": url},
            log=log, progress=lambda f, m="": progress(min(0.999, f), desc=m or "Working…"))
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"{e}")
    report += f"\n\nSaved in: {os.path.dirname(path)}"
    return path, report, script_text


def build():
    blocks_kw = {"title": "Story Video Studio"}
    theme = gr.themes.Soft(primary_hue="pink", secondary_hue="violet")
    if "theme" in inspect.signature(gr.Blocks.__init__).parameters:
        blocks_kw.update(theme=theme, css=CSS)

    with gr.Blocks(**blocks_kw) as demo:
        gr.Markdown("# 🎬 Story Video Studio\nType a story → get an animated, talking, captioned video "
                    "for TikTok / Reels / Shorts (vertical 1080×1920).")
        with gr.Row():
            with gr.Column(scale=3):
                story = gr.Textbox(label="Your story, idea, or script", lines=16,
                                   placeholder="Type a story or an idea here…")
                with gr.Row():
                    ex1 = gr.Button("Example idea (for AI)", size="sm")
                    ex2 = gr.Button("Example script (no AI)", size="sm")
                with gr.Accordion("How to write", open=False):
                    gr.Markdown(HELP)
            with gr.Column(scale=2):
                title = gr.Textbox(label="Title / hook shown at the start (optional)",
                                   placeholder="e.g. She found a DRAGON egg 😳")
                duration = gr.Radio(list(pipeline.DURATIONS), value="1 min", label="Video length")
                quality = gr.Radio(list(pipeline.QUALITIES), value=list(pipeline.QUALITIES)[0], label="Quality")
                music = gr.Dropdown(MOODS, value="Happy", label="Background music (royalty-free, auto-made)")
                music_file = gr.File(label="…or your own music file (optional)", type="filepath",
                                     file_types=[".mp3", ".wav", ".m4a", ".ogg"])
                narrator = gr.Dropdown([v for v in voices.VOICE_LABELS if v != "Auto"],
                                       value=voices.NARRATOR_DEFAULT, label="Narrator voice")
                end_text = gr.Textbox(label="End screen text", value="Follow for more stories!")

        with gr.Accordion("🤖 AI writer – turns a short idea into a full script of the chosen length", open=True):
            with gr.Row():
                provider = gr.Dropdown(ai_writer.PROVIDERS, value=ai_writer.PROVIDER_NONE, label="AI provider")
                key = gr.Textbox(label="API key", type="password", visible=False)
                model = gr.Textbox(label="Model", visible=False)
                url = gr.Textbox(label="Server URL", visible=False)
            write_btn = gr.Button("✍️ Write the script with AI first (so I can edit it)", size="sm")

        with gr.Accordion("🧑‍🤝‍🧑 Characters (optional) – choose voice, look, or put a real face on someone",
                          open=False):
            gr.Markdown("Type the character's name exactly as in the story. Leave empty for automatic "
                        "cartoon characters and voices.")
            char_fields = []
            for i in range(N_CHARS):
                with gr.Row(elem_classes="char-row"):
                    n = gr.Textbox(label=f"Character {i + 1} name", scale=2)
                    lk = gr.Dropdown(voices.LOOKS, value="Auto", label="Look", scale=1)
                    vc = gr.Dropdown(voices.VOICE_LABELS, value="Auto", label="Voice", scale=2)
                    ph = gr.Image(label="Face photo", type="filepath", height=130, scale=1)
                char_fields += [n, lk, vc, ph]

        gen = gr.Button("🎬 Generate video", variant="primary", elem_id="gen-btn")
        with gr.Row():
            video = gr.Video(label="Your video", height=640)
            with gr.Column():
                status = gr.Textbox(label="Result", lines=10)
                script_out = gr.Textbox(label="Script that was used (copy it into the story box to edit & re-make)",
                                        lines=12)

        provider.change(_on_provider, provider, [model, url, key])
        ex1.click(lambda: EXAMPLE_IDEA, None, story)
        ex2.click(lambda: EXAMPLE_SCRIPT, None, story)
        write_btn.click(write_with_ai, [story, duration, provider, key, model, url] + char_fields, story)
        gen.click(make_video, [story, title, duration, quality, music, music_file, narrator, end_text,
                               provider, key, model, url] + char_fields, [video, status, script_out])
    return demo, theme


def main():
    demo, theme = build()
    launch_kw = {"inbrowser": True}
    if "theme" in inspect.signature(demo.launch).parameters:
        launch_kw.update(theme=theme, css=CSS)
    demo.queue().launch(**launch_kw)


if __name__ == "__main__":
    main()
