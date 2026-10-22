"""Test signal for the visualizer: log sweep 40 Hz-16 kHz (24 s) + 10 beeps of 1 kHz (1/s)."""
import math, struct, sys, wave

RATE, AMP = 48000, 10 ** (-18 / 20) * 32767  # -18 dBFS
f0, f1, dur = 40.0, 16000.0, 24.0
samples = []
k = math.log(f1 / f0)
for n in range(int(dur * RATE)):
    t = n / RATE
    # phase of an exponential sweep; short fade in/out to avoid clicks
    phase = 2 * math.pi * f0 * dur / k * (math.exp(t / dur * k) - 1)
    env = min(1.0, t / 0.05, (dur - t) / 0.05)
    samples.append(AMP * env * math.sin(phase))
samples += [0.0] * RATE  # 1 s of silence
for _ in range(10):
    for n in range(RATE):
        t = n / RATE
        if t < 0.1:
            env = min(1.0, t / 0.005, (0.1 - t) / 0.005)
            samples.append(AMP * env * math.sin(2 * math.pi * 1000 * t))
        else:
            samples.append(0.0)
with wave.open(sys.argv[1], "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(b"".join(struct.pack("<h", int(s)) for s in samples))
print(f"{len(samples) / RATE:.0f} s, {len(samples)} samples")
