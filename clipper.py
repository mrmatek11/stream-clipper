"""stream-clipper: stream VOD -> best moments -> vertical 1080x1920 clips with burned-in subtitles.
Runs fully locally (yt-dlp + faster-whisper on CUDA + ffmpeg NVENC). No API keys, no tokens.

    python clipper.py                          # every group/channel in config.toml
    python clipper.py --url <VOD> [--group X]  # one VOD
    python clipper.py --frame <VOD> --at 600   # screenshot with a 10% grid, to set `facecam`
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

HYPE = re.compile(r"KEKW|LUL|OMEGALUL|LMAO|POG|\bW+\b|xD+|HAHA|\?\?\?|\bclip|😂|💀", re.I)


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
    return subprocess.run(cmd, check=True, **kw)


def channel_url(ch):
    """'twitch:name' / 'youtube:name' / 'kick:name' shorthands, or any URL yt-dlp understands."""
    site, _, name = ch.partition(":")
    return {
        "twitch": f"https://www.twitch.tv/{name}/videos?filter=archives&sort=time",
        "youtube": f"https://www.youtube.com/@{name}/streams",
        "kick": f"https://kick.com/{name}/videos",
    }.get(site, ch)


def list_vods(url, n):
    out = run(["yt-dlp", "--flat-playlist", "--playlist-end", str(n), "--print", "%(url)s %(id)s %(title)s", url],
              capture_output=True, text=True, encoding="utf-8").stdout
    return [line.split(" ", 2) for line in out.splitlines() if line.count(" ") >= 2]


# ---------- finding moments ----------

def twitch_chat(url, work):
    """Needs TwitchDownloaderCLI on PATH (https://github.com/lay295/TwitchDownloader/releases)."""
    p = work / "chat.json"
    run(["TwitchDownloaderCLI", "chatdownload", "--id", url, "-o", str(p), "--collision", "Overwrite"],
        capture_output=True)
    return [(c["content_offset_seconds"], c["message"]["body"])
            for c in json.loads(p.read_text(encoding="utf-8"))["comments"]]


def youtube_chat(url, work):
    """Live-chat replay of a past YouTube stream, via yt-dlp. [] if the video has none."""
    run(["yt-dlp", "-q", "--no-warnings", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
         "-o", str(work / "yt"), url])
    p = work / "yt.live_chat.json"
    if not p.exists():
        return []
    events = []
    for line in p.open(encoding="utf-8"):
        a = json.loads(line).get("replayChatItemAction", {})
        for act in a.get("actions", []):
            r = act.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
            if r:
                text = "".join(x.get("text", "") for x in r["message"].get("runs", []))
                events.append((int(a["videoOffsetTimeMsec"]) / 1000, text))
    return events


def chat_curve(events):
    """[(second, text)] -> per-second activity; hype messages count triple."""
    c = Counter()
    for t, text in events:
        if t >= 0:
            c[int(t)] += 3 if HYPE.search(text or "") else 1
    return [c[i] for i in range(max(c) + 1)] if c else []


def audio_curve(url):
    """Per-second loudness (mean energy) of the whole VOD, streamed audio-only - nothing saved to disk."""
    import numpy as np
    dl = subprocess.Popen(["yt-dlp", "-q", "--no-warnings", "-f", "ba/b", "-N", "8", "-o", "-", url],
                          stdout=subprocess.PIPE)
    ff = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "pipe:", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                          stdin=dl.stdout, stdout=subprocess.PIPE)
    dl.stdout.close()
    curve = []
    while chunk := ff.stdout.read(16000):  # 1 s of 8 kHz 16-bit mono
        a = np.frombuffer(chunk[:len(chunk) // 2 * 2], np.int16).astype(np.float32)
        curve.append(float((a * a).mean()))
    ff.wait()
    dl.wait()
    return curve


def peaks(curve, n, window, min_gap, min_ratio):
    """Top-n non-overlapping `window`-second stretches where the curve jumps vs. its median.
    Returns [(start_second, ratio)], best first."""
    if len(curve) <= window:
        return []
    pre = [0.0]
    for v in curve:
        pre.append(pre[-1] + v)
    roll = [pre[i + window] - pre[i] for i in range(len(curve) - window + 1)]
    base = sorted(roll)[len(roll) // 2] or 1
    picked = []
    for i in sorted(range(len(roll)), key=roll.__getitem__, reverse=True):
        ratio = roll[i] / base
        if ratio < min_ratio or len(picked) == n:
            break
        if all(abs(i - p) >= min_gap for p, _ in picked):
            picked.append((i, ratio))
    return picked


def moments(url, work, s):
    """Chat spikes when we can get chat, loudness spikes otherwise (works for any site yt-dlp supports)."""
    events = []
    try:
        if "twitch.tv" in url and shutil.which("TwitchDownloaderCLI"):
            events = twitch_chat(url, work)
        elif "youtube.com" in url or "youtu.be" in url:
            events = youtube_chat(url, work)
    except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as e:
        print(f"  chat unavailable ({e}), using audio")
    hits = peaks(chat_curve(events), s["clips_per_vod"], s["window"], s["min_gap"], s["chat_min_ratio"])
    if hits:
        print(f"  {len(events)} chat messages -> {len(hits)} moments")
        return hits
    print("  analysing audio loudness (whole VOD, audio only)...")
    hits = peaks(audio_curve(url), s["clips_per_vod"], s["window"], s["min_gap"], s["audio_min_ratio"])
    print(f"  {len(hits)} loud moments")
    return hits


# ---------- making the clip ----------

def ass_time(t):
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def make_ass(words, words_per_line, font_size, margin_v):
    """words: [(start, end, text)] -> ASS subtitles, short-form style (a few big words at a time)."""
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


CAM_H = 640  # facecam strip height in the vertical layout


def video_filter(s, facecam):
    if facecam:  # streamer's cam on top, gameplay (center crop) below
        x, y, w, h = facecam
        return (f"[0:v]split[a][b];[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},"
                f"scale=1080:{CAM_H}:force_original_aspect_ratio=increase,crop=1080:{CAM_H}[top];"
                f"[b]scale=-2:{1920 - CAM_H},crop=1080:{1920 - CAM_H}[bot];[top][bot]vstack,ass=subs.ass[v]")
    zw = int(1080 * s["zoom"]) // 2 * 2  # blurred background + zoomed full frame in the middle
    return ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
            f"[0:v]scale={zw}:-2,crop=1080:ih[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,ass=subs.ass[v]")


def render(clip_dir, out_file, s, facecam=None):
    q = ["-cq", str(s["quality"]), "-preset", "p5"] if "nvenc" in s["encoder"] else ["-crf", str(s["quality"]),
                                                                                    "-preset", "veryfast"]
    # cwd=clip_dir + relative "subs.ass": avoids ffmpeg filter escaping of Windows "C:\" paths
    try:
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", "raw.mp4", "-filter_complex", video_filter(s, facecam),
             "-map", "[v]", "-map", "0:a?", "-c:v", s["encoder"], *q, "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_file.resolve())], cwd=clip_dir)
    except subprocess.CalledProcessError:
        if s["encoder"] == "libx264":
            raise
        print(f"  {s['encoder']} failed, falling back to CPU (libx264)")
        s["encoder"] = "libx264"
        render(clip_dir, out_file, s, facecam)


_model = None


def whisper(s):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel(s["whisper_model"], device=s["device"],
                                  compute_type="float16" if s["device"] == "cuda" else "int8")
        except Exception as e:  # no NVIDIA GPU / driver problems
            print(f"  GPU Whisper failed ({e}), using CPU with 'small' model")
            _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def process_vod(url, vid, group, s):
    work = Path(s["work_dir"]) / vid
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(s["out_dir"]) / group.get("name", "manual")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        hits = moments(url, work, s)
        for n, (sec, ratio) in enumerate(hits, 1):
            start, end = max(0, sec - s["before"]), sec + s["window"] + s["after"]
            name = f"{ratio:05.1f}x_{vid}_{start // 3600}h{start // 60 % 60:02}m{start % 60:02}s"
            clip_dir = work / str(n)
            clip_dir.mkdir(exist_ok=True)
            try:
                run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=1080]+ba/b", "--download-sections",
                     f"*{start}-{end}", "--merge-output-format", "mp4", "-o", str(clip_dir / "raw.%(ext)s"), url])
                segs, _ = whisper(s).transcribe(str(clip_dir / "raw.mp4"), word_timestamps=True, vad_filter=True,
                                                language=s["language"] or None)
                words = [(w.start, w.end, w.word) for seg in segs for w in seg.words]
                margin = 1920 - CAM_H - 140 if group.get("facecam") else s["margin_v"]
                (clip_dir / "subs.ass").write_text(
                    make_ass(words, s["words_per_line"], s["font_size"], margin), encoding="utf-8")
                render(clip_dir, out_dir / f"{name}.mp4", s, group.get("facecam"))
                said = "".join(w[2] for w in words).strip()
                (out_dir / f"{name}.txt").write_text(
                    f"{said[:90]}\n\n{group.get('hashtags', '')}\n\nSource: {url}?t={start}s\n", encoding="utf-8")
                print(f"  -> {out_dir / name}.mp4")
            except subprocess.CalledProcessError as e:
                print(f"  clip {n} failed: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def frame(url, at, s):
    """Grab one frame with a 10% grid so you can read `facecam = [x, y, w, h]` off it."""
    out = Path(s["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "frame_src.mp4"
    run(["yt-dlp", "-q", "--no-warnings", "-f", "bv*[height<=1080]/b", "--download-sections", f"*{at}-{at + 2}",
         "--force-overwrites", "-o", str(tmp), url])
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-frames:v", "1",
         "-vf", "drawgrid=w=iw/10:h=ih/10:t=2:c=red@0.8", str(out / "frame.png")])
    tmp.unlink()
    print(f"saved {out / 'frame.png'} - red lines every 0.1 of width/height")


def main():
    cuda_libs()
    ap = argparse.ArgumentParser(description="Turn stream VODs into vertical clips.")
    ap.add_argument("config", nargs="?", default="config.toml")
    ap.add_argument("--url", help="process just this VOD")
    ap.add_argument("--group", help="with --url: use this group's hashtags/facecam/output folder")
    ap.add_argument("--frame", metavar="URL", help="save a gridded screenshot to help set facecam")
    ap.add_argument("--at", type=int, default=600, help="with --frame: second of the VOD")
    a = ap.parse_args()
    cfg = tomllib.loads(Path(a.config).read_text(encoding="utf-8"))
    s, groups = cfg["settings"], cfg.get("group", [])

    if a.frame:
        return frame(a.frame, a.at, s)
    if a.url:
        group = next((g for g in groups if g["name"] == a.group), {})
        return process_vod(a.url, re.sub(r"\W", "", a.url)[-12:], group, s)

    done_file = Path(s["out_dir"]) / ".done"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    for group in groups:
        kw = [k.lower() for k in group.get("keywords", [])]
        for ch in group["channels"]:
            print(f"[{group['name']}] {ch}")
            try:
                vods = list_vods(channel_url(ch), s["vods_per_channel"])
            except subprocess.CalledProcessError as e:
                print(f"  can't list VODs: {e}")
                continue
            for url, vid, title in vods:
                if vid in done or (kw and not any(k in title.lower() for k in kw)):
                    continue
                print(f" {title}")
                process_vod(url, vid, group, s)
                done.add(vid)
                done_file.parent.mkdir(parents=True, exist_ok=True)
                with done_file.open("a") as f:
                    f.write(vid + "\n")


if __name__ == "__main__":
    main()
