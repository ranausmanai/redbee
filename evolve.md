# Fitness Criteria: Sorting Algorithm

## Goal
Evolve the fastest possible pure-Python sorting function.

## Constraints
- Must be a single function: `def sort(arr)` that takes a list of numbers and returns a new sorted list
- Pure Python only — no imports allowed (no numpy, no ctypes, no built-in sorted/sort)
- Must handle: empty lists, single element, duplicates, negative numbers, already sorted, reverse sorted
- Must be CORRECT — wrong results = score 0

## What "better" means (in priority order)
1. **Correctness** — must produce correctly sorted output for all cases
2. **Speed** — faster on large arrays (10k+ elements) wins
3. **Cleverness** — novel algorithmic approaches score higher than textbook implementations

## Scoring Guide
- 0-2: broken or incorrect
- 3-4: correct but naive (bubble sort, selection sort)
- 5-6: decent algorithm (basic quicksort, mergesort)
- 7-8: well-optimized (hybrid approaches, good pivot selection, insertion sort for small arrays)
- 9-10: exceptional (novel optimizations, competitive with built-in sort for the constraints)
