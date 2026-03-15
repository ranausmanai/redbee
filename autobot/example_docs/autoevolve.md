# AutoEvolve

AutoEvolve uses LLM-powered natural selection to optimize any code or text artifact.

## Usage
```bash
# Evolve with a benchmark
python3 autoevolve.py seed.py evolve.md -b "python3 bench.py {file}"

# Evolve with just LLM judging (no benchmark)
python3 autoevolve.py prompt.txt criteria.md --target 9.0

# Use codex with low reasoning effort
python3 autoevolve.py seed.py evolve.md -e codex --reasoning low

# Stop after 5 generations or if no improvement for 3
python3 autoevolve.py seed.py evolve.md -g 5 --patience 3
```

## How it works
1. Score the seed file against fitness criteria
2. LLM generates N mutations (different strategies)
3. Each mutation is benchmarked (if benchmark provided) and scored by LLM judge
4. Best mutation survives, becomes the new parent
5. Repeat for G generations

## Flags
- `-g` — max generations (default: 10)
- `-n` — mutations per generation (default: 3)
- `-b` — benchmark command ({file} gets replaced with temp file path)
- `-e` — engine: claude or codex
- `--reasoning` — codex reasoning effort: low, medium, high
- `--target` — stop when score reaches this value
- `--patience` — stop after N generations with no improvement
- `--timeout` — stop after N seconds total

## Use cases
- Optimize algorithms for speed (with benchmark timing)
- Evolve prompts for accuracy (with benchmark testing against real data)
- Optimize landing pages for Lighthouse scores
- Evolve any text: tweets, emails, resumes, comedy bits
