"""stream-clipper: VOD -> chat spikes -> vertical 1080x1920 clips with burned-in subtitles.
Runs fully locally (yt-dlp + chat-downloader + faster-whisper on CUDA + ffmpeg NVENC). No API, no tokens.

    python clipper.py                      # all games/channels from config.toml
    python clipper.py --url <VOD url>      # one VOD, e.g. to test settings
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

HYPE = re.compile(r"KEKW|LUL|OMEGALUL|LMAO|POG|\bW+\b|xD+|HAHA|\?\?\?|\bclip", re.I)


def cuda_libs():
    """Point the loader at pip's nvidia-cublas/cudnn so faster-whisper finds them without a CUDA install."""
    try:
        import nvidia
    except ImportError:
        return
    dirs = [str(d) for root in nvidia.__path__ for d in Path(root).glob("*/bin" if os.name == "nt" else "*/lib")]
    if not dirs:
        return
    if os.name == "nt":
        for d in dirs:
            os.add_dll_directory(d)
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
    elif dirs[0] not in os.environ.get("LD_LIBRARY_PATH", ""):
        # Linux reads LD_LIBRARY_PATH only at process start, so restart once with it set.
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(dirs + [os.environ.get("LD_LIBRARY_PATH", "")])
        os.execv(sys.executable, [sys.executable, *sys.argv])


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def list_vods(channel_url, n):
    out = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(n), "--print", "%(url)s\t%(id)s\t%(title)s", channel_url],
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return [line.split("\t", 2) for line in out.splitlines() if line.count("\t") >= 2]


def chat_events(url):
    """[(second, weight)] - hype messages count triple."""
    from chat_downloader import ChatDownloader
    events = []
    for m in ChatDownloader().get_chat(url):
        t = m.get("time_in_seconds")
        if t is not None and t >= 0:
            events.append((t, 3 if HYPE.search(m.get("message") or "") else 1))
    return events


def peaks(events, n, window, min_gap, min_ratio):
    """Top-n non-overlapping `window`-second stretches where chat activity jumps vs. the VOD's median.
    Returns [(start_second, ratio)], best first."""
    per_sec = Counter()
    for t, w in events:
        per_sec[int(t)] += w
    if not per_sec:
        return []
    length = max(per_sec) + 1
    roll = [sum(per_sec[s] for s in range(i, i + window)) for i in range(length)]
    # ponytail: O(length*window) rolling sum, fine for 12h VODs; switch to prefix sums if it ever matters
    base = sorted(roll)[len(roll) // 2] + 1
    picked = []
    for i in sorted(range(length), key=roll.__getitem__, reverse=True):
        ratio = roll[i] / base
        if ratio < min_ratio or len(picked) == n:
            break
        if all(abs(i - p) >= min_gap for p, _ in picked):
            picked.append((i, ratio))
    return picked


def ass_time(t):
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def make_ass(words, words_per_line, font_size, margin_v):
    """words: [(start, end, text)] -> ASS subtitle file contents, short-form style (few big words at a time)."""
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Default,Arial,{font_size},&H00FFFFFF,&H00000000,&H80000000,1,1,6,2,2,60,60,{margin_v}\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for i in range(0, len(words), words_per_line):
        g = words[i:i + words_per_line]
        text = " ".join(w[2].strip() for w in g).upper().replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ass_time(g[0][0])},{ass_time(g[-1][1])},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + "\n"


def render(clip_dir, out_file, s):
    zw = int(1080 * s["zoom"]) // 2 * 2
    vf = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
        f"[0:v]scale={zw}:-2,crop=1080:ih[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=subs.ass[v]"
    )
    q = ["-cq", str(s["quality"])] if "nvenc" in s["encoder"] else ["-crf", str(s["quality"])]
    # cwd=clip_dir + relative "subs.ass": avoids ffmpeg filter escaping hell with Windows "C:\" paths
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", "raw.mp4", "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
         "-c:v", s["encoder"], *q, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(out_file.resolve())], cwd=clip_dir)


def process_vod(url, vid, out_dir, s, model):
    print(f"  chat: {url}")
    try:
        events = chat_events(url)
    except Exception as e:  # sub-only VOD, no chat replay, site change...
        print(f"  skip, no chat: {e}")
        return
    hits = peaks(events, s["clips_per_vod"], s["window"], s["min_gap"], s["min_ratio"])
    print(f"  {len(events)} messages, {len(hits)} peaks")
    out_dir.mkdir(parents=True, exist_ok=True)
    for n, (sec, ratio) in enumerate(hits, 1):
        start, end = max(0, sec - s["before"]), sec + s["window"] + s["after"]
        clip_dir = Path(s["work_dir"]) / f"{vid}_{n}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        try:
            run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=1080]+ba/b", "--download-sections",
                 f"*{start}-{end}", "--merge-output-format", "mp4", "-o", str(clip_dir / "raw.%(ext)s"), url])
            segs, _ = model.transcribe(str(clip_dir / "raw.mp4"), word_timestamps=True, vad_filter=True,
                                       language=s["language"] or None)
            words = [(w.start, w.end, w.word) for seg in segs for w in seg.words]
            (clip_dir / "subs.ass").write_text(
                make_ass(words, s["words_per_line"], s["font_size"], s["margin_v"]), encoding="utf-8")
            out = out_dir / f"{ratio:05.1f}x_{vid}_{n}_{start // 3600}h{start // 60 % 60:02}m.mp4"
            render(clip_dir, out, s)
            print(f"  -> {out}")
        except subprocess.CalledProcessError as e:
            print(f"  clip {n} failed: {e}")
        finally:
            shutil.rmtree(clip_dir, ignore_errors=True)


def main():
    cuda_libs()
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?", default="config.toml")
    ap.add_argument("--url", help="process just this VOD into <out_dir>/manual")
    a = ap.parse_args()
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    s = cfg["settings"]

    from faster_whisper import WhisperModel
    model = WhisperModel(s["whisper_model"], device=s["device"],
                         compute_type="float16" if s["device"] == "cuda" else "int8")
    out_root = Path(s["out_dir"])

    if a.url:
        process_vod(a.url, re.sub(r"\W", "", a.url)[-12:], out_root / "manual", s, model)
        return

    done_file = out_root / ".done"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    for game in cfg["game"]:
        kw = [k.lower() for k in game.get("keywords", [])]
        for ch in game["channels"]:
            print(f"[{game['name']}] {ch}")
            try:
                vods = list_vods(ch, s["vods_per_channel"])
            except subprocess.CalledProcessError as e:
                print(f"  can't list VODs: {e}")
                continue
            for url, vid, title in vods:
                if vid in done or (kw and not any(k in title.lower() for k in kw)):
                    continue
                print(f" {title}")
                process_vod(url, vid, out_root / game["name"], s, model)
                done.add(vid)
                out_root.mkdir(parents=True, exist_ok=True)
                with done_file.open("a") as f:
                    f.write(vid + "\n")


if __name__ == "__main__":
    main()
