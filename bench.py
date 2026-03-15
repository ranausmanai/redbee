#!/usr/bin/env python3
"""Benchmark for sorting evolution. Usage: python3 bench.py <candidate.py>"""
import sys, time, random, importlib.util

# load candidate
spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    print(f"LOAD ERROR: {e}")
    sys.exit(0)

sort_fn = getattr(mod, 'sort', None)
if not sort_fn:
    print("ERROR: no sort() function found")
    sys.exit(0)

# correctness tests
tests = [
    ([], []),
    ([1], [1]),
    ([3, 1, 2], [1, 2, 3]),
    ([5, 5, 5], [5, 5, 5]),
    ([-3, -1, -2], [-3, -2, -1]),
    (list(range(100)), list(range(100))),
    (list(range(100, 0, -1)), list(range(1, 101))),
    ([4, 2, 7, 2, 1, 7, 9, 0], [0, 1, 2, 2, 4, 7, 7, 9]),
]

for inp, expected in tests:
    try:
        result = sort_fn(inp.copy())
        if result != expected:
            print(f"WRONG: sort({inp[:10]}...) = {result[:10]}... expected {expected[:10]}...")
            sys.exit(0)
    except Exception as e:
        print(f"CRASH: {e}")
        sys.exit(0)

print("CORRECTNESS: PASS")

# speed benchmark
random.seed(42)
sizes = [1000, 5000, 10000]
for size in sizes:
    arr = [random.randint(-10000, 10000) for _ in range(size)]
    t0 = time.perf_counter()
    try:
        result = sort_fn(arr.copy())
        dt = time.perf_counter() - t0
        print(f"SIZE {size}: {dt:.4f}s")
    except Exception as e:
        print(f"SIZE {size}: CRASH ({e})")

# overall timing on 10k
arr = [random.randint(-10000, 10000) for _ in range(10000)]
times = []
for _ in range(3):
    t0 = time.perf_counter()
    sort_fn(arr.copy())
    times.append(time.perf_counter() - t0)
avg = sum(times) / len(times)
print(f"AVG 10k: {avg:.4f}s")
