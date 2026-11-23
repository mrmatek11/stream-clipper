from clipper import ass_time, chat_curve, channel_url, make_ass, peaks

# quiet chat at 1 msg/s with one KEKW burst at 500 s
ev = [(t, "hi") for t in range(1000)] + [(500 + i / 10, "KEKW") for i in range(100)]
p = peaks(chat_curve(ev), n=5, window=10, min_gap=120, min_ratio=3)
assert len(p) == 1 and 491 <= p[0][0] <= 500, p
assert peaks([], 5, 10, 120, 3) == [] and chat_curve([]) == []

# loud audio stretch at 300 s, two separate loud stretches -> two clips, best first
curve = [1.0] * 1000
curve[300:310] = [10.0] * 10
curve[700:710] = [6.0] * 10
p = peaks(curve, n=5, window=10, min_gap=120, min_ratio=2.5)
assert [x[0] for x in p] == [300, 700], p

assert channel_url("twitch:abc") == "https://www.twitch.tv/abc/videos?filter=archives&sort=time"
assert channel_url("https://x.y/z") == "https://x.y/z"

assert ass_time(3725.5) == "1:02:05.50"
a = make_ass([(0, .5, " hi"), (.5, 1, " {x}"), (1, 2, " yo"), (2, 3, " end")], 3, 80, 500)
assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,HI (X) YO" in a
assert a.rstrip().endswith(",END")
print("ok")
