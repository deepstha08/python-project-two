"""Command-line version (the app is easier: run app.py).

Example:
    python cli.py my_story.txt --minutes 3 --music Happy
    python cli.py my_story.txt --minutes 10 --ai claude --key sk-ant-...
"""
import argparse

from engine import ai_writer, pipeline, voices

AI = {"none": ai_writer.PROVIDER_NONE, "claude": ai_writer.PROVIDER_CLAUDE, "openai": ai_writer.PROVIDER_OPENAI,
      "ollama": ai_writer.PROVIDER_OLLAMA}


def main():
    ap = argparse.ArgumentParser(description="Turn a story into an animated TikTok video.")
    ap.add_argument("story_file", help="text file with your story or script")
    ap.add_argument("--minutes", type=int, default=1, choices=[1, 3, 5, 8, 10, 15])
    ap.add_argument("--title", default="")
    ap.add_argument("--fast", action="store_true", help="720p instead of 1080p")
    ap.add_argument("--music", default="Happy", choices=["Happy", "Calm", "Funny", "Suspense", "Sad", "None"])
    ap.add_argument("--end-text", default="Follow for more stories!")
    ap.add_argument("--ai", default="none", choices=list(AI))
    ap.add_argument("--key", default="", help="API key for the AI writer")
    ap.add_argument("--model", default="")
    ap.add_argument("--photo", action="append", default=[], metavar="NAME=PATH",
                    help="put a real face on a character, e.g. --photo Mia=mia.jpg")
    a = ap.parse_args()

    with open(a.story_file, encoding="utf-8") as f:
        story = f.read()
    chars = []
    for item in a.photo:
        name, _, path = item.partition("=")
        chars.append({"name": name, "photo": path, "look": "Auto", "voice": "Auto"})
    quality = list(pipeline.QUALITIES)[1 if a.fast else 0]
    path, script, report = pipeline.generate(
        story, a.minutes, a.title, quality, a.music, None, voices.NARRATOR_DEFAULT, a.end_text, chars,
        {"provider": AI[a.ai], "api_key": a.key, "model": a.model, "base_url": ""},
        progress=lambda f, m="": print(f"\r{int(f * 100):3d}%  {m:<40}", end="", flush=True))
    print("\n" + report)


if __name__ == "__main__":
    main()
