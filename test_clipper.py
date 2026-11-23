from clipper import ass_time, make_ass, peaks

# quiet chat at 1 msg/s with one burst at 500s
ev = [(t, 1) for t in range(1000)] + [(500 + i / 10, 3) for i in range(100)]
p = peaks(ev, n=5, window=10, min_gap=120, min_ratio=3)
assert len(p) == 1 and 491 <= p[0][0] <= 500, p
assert peaks([], 5, 10, 120, 3) == []

assert ass_time(3725.5) == "1:02:05.50"
a = make_ass([(0, .5, " hi"), (.5, 1, " {x}"), (1, 2, " yo"), (2, 3, " end")], 3, 80, 500)
assert "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,HI (X) YO" in a
assert a.rstrip().endswith(",END")
print("ok")
