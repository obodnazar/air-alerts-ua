"""Compare a fresh run with the committed results: python scripts/check_results.py <committed_summary.json>"""
import json
import sys

ref, new = json.load(open(sys.argv[1])), json.load(open("results/summary.json"))
keys = [("test", "m1_20:00", "bss_vs_strongest_ci", "logit"), ("test", "m1_07:00", "bss_vs_strongest_ci", "logit"),
        ("test", "m2", "bss_vs_km_ci", "logit")]
bad = []
for k in keys:
    a, b = ref, new
    for p in k:
        a, b = a[p], b[p]
    if any(abs(x - y) > 1e-6 for x, y in zip(a, b)):
        bad.append((k, a, b))
for f in ("m1_features_20:00", "m1_features_07:00", "m2_features"):
    if ref[f]["chosen"] != new[f]["chosen"]:
        bad.append((f, ref[f]["chosen"], new[f]["chosen"]))
print("results match the committed ones" if not bad else f"MISMATCH: {bad}")
sys.exit(1 if bad else 0)
