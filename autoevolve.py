#!/usr/bin/env python3
"""autoevolve — natural selection, but the mutations are intelligent.

Uses CLI tools (claude or codex) instead of API keys.

Usage:
  python3 autoevolve.py seed.py evolve.md -b "python3 bench.py {file}"
  python3 autoevolve.py seed.py evolve.md -e codex --target 9.0 --patience 3 --timeout 600
"""

import json
import sys
import os
import subprocess
import tempfile
import time
import argparse
from pathlib import Path

def read(path):
    return Path(path).read_text()

def llm(prompt, engine="claude", reasoning="medium"):
    """Call an LLM via CLI. Returns raw text response."""
    if engine == "claude":
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        result = subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True, text=True, timeout=300, env=env
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude error: {result.stderr[:200]}")
        return result.stdout.strip()
    elif engine == "codex":
        result = subprocess.run(
            ["codex", "exec", "-c", f'model_reasoning_effort="{reasoning}"', prompt],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            raise RuntimeError(f"codex error: {result.stderr[:200]}")
        return result.stdout.strip()
    else:
        raise ValueError(f"Unknown engine: {engine}")

def extract_json(text):
    """Extract JSON from LLM response (handles markdown fences etc)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find('[')
    end = text.rfind(']') + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    start = text.find('{')
    end = text.rfind('}') + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from response:\n{text[:300]}...")

def mutate(code, criteria, history, n=3, engine="claude", reasoning="medium"):
    """Generate n mutations of the code using LLM."""
    history_str = ""
    if history:
        history_str = "\n\nPrevious attempts and their scores (learn from these):\n"
        for h in history[-10:]:
            history_str += f"- Gen {h['gen']}: score {h['score']:.1f}/10 — {h['summary']}\n"

    prompt = f"""You are an evolution engine. Your job is to create {n} improved mutations of the code below.

## Fitness Criteria
{criteria}

## Current Code
```
{code}
```
{history_str}

## Rules
- Each mutation should try a DIFFERENT strategy to improve fitness
- Mutations must be COMPLETE, runnable code (not fragments)
- Be creative but stay within the constraints
- Learn from history: don't repeat failed strategies, double down on what worked

Return a JSON array of exactly {n} mutations. Each mutation is an object with:
- "code": the complete mutated code (string)
- "strategy": one-line description of what you changed and why

Return ONLY valid JSON, no markdown fences, no explanation."""

    resp = llm(prompt, engine, reasoning)
    result = extract_json(resp)
    if isinstance(result, dict):
        result = [result]
    return result

def judge(code, criteria, bench_result=None, engine="claude", reasoning="medium"):
    """LLM-as-judge scores a mutation. Returns (score, explanation)."""
    bench_ctx = ""
    if bench_result is not None:
        bench_ctx = f"\n\n## Benchmark Result\n```\n{bench_result}\n```"

    prompt = f"""Score this code against the fitness criteria. Be harsh and precise.

## Fitness Criteria
{criteria}
{bench_ctx}

## Code
```
{code}
```

Return ONLY a JSON object with:
- "score": number from 0-10 (use decimals, e.g. 7.3)
- "explanation": one sentence on why this score

Return ONLY valid JSON, no markdown."""

    resp = llm(prompt, engine, reasoning)
    result = extract_json(resp)
    return result["score"], result["explanation"]

def run_bench(code, bench_cmd, timeout=30):
    """Run optional benchmark command. Returns stdout or None."""
    if not bench_cmd:
        return None
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir='.')
    tmp.write(code)
    tmp.close()
    try:
        cmd = bench_cmd.replace("{file}", tmp.name)
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        output = result.stdout + result.stderr
        return output if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return "(timeout)"
    except Exception as e:
        return f"(error: {e})"
    finally:
        os.unlink(tmp.name)

def evolve(seed_path, criteria_path, generations=10, population=3, bench_cmd=None,
           engine="claude", target=None, patience=None, time_budget=None, reasoning="medium"):
    """Main evolution loop with smart stopping."""
    criteria = read(criteria_path)
    current_code = read(seed_path)
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"  autoevolve — natural selection, but intelligent")
    print(f"{'='*60}")
    print(f"  seed:        {seed_path}")
    print(f"  criteria:    {criteria_path}")
    print(f"  engine:      {engine}")
    print(f"  generations: {generations} max")
    print(f"  population:  {population}/gen")
    if bench_cmd:
        print(f"  benchmark:   {bench_cmd}")
    # stopping conditions
    stops = []
    if target:
        stops.append(f"target score >= {target}")
    if patience:
        stops.append(f"no improvement for {patience} gens")
    if time_budget:
        stops.append(f"time budget {time_budget}s")
    if stops:
        print(f"  stop when:   {' OR '.join(stops)}")
    print(f"{'='*60}\n")

    # score the seed
    print("  Scoring seed...", end=" ", flush=True)
    bench_result = run_bench(current_code, bench_cmd)
    score, explanation = judge(current_code, criteria, bench_result, engine, reasoning)
    best_score = score
    best_code = current_code
    history = []
    no_improve_count = 0

    print(f"\n  GEN 0 (seed): {score:.1f}/10 — {explanation}\n")

    for gen in range(1, generations + 1):
        # check time budget
        if time_budget and (time.time() - start_time) >= time_budget:
            print(f"\n  STOP: time budget ({time_budget}s) reached")
            break

        t0 = time.time()
        print(f"  GEN {gen}/{generations} ", end="", flush=True)

        # mutate
        try:
            mutations = mutate(best_code, criteria, history, n=population, engine=engine, reasoning=reasoning)
        except Exception as e:
            print(f"✗ mutation failed: {e}")
            continue

        # evaluate each mutation
        gen_best_score = best_score
        gen_best_code = best_code
        gen_best_strategy = "no improvement"

        for i, m in enumerate(mutations):
            code = m["code"]
            strategy = m.get("strategy", "unknown")

            try:
                bench_result = run_bench(code, bench_cmd)
                s, exp = judge(code, criteria, bench_result, engine, reasoning)
            except Exception as e:
                print("✗", end="", flush=True)
                continue

            if s > gen_best_score:
                gen_best_score = s
                gen_best_code = code
                gen_best_strategy = strategy
                print("↑", end="", flush=True)
            else:
                print("·", end="", flush=True)

        dt = time.time() - t0

        if gen_best_score > best_score:
            improvement = gen_best_score - best_score
            best_score = gen_best_score
            best_code = gen_best_code
            no_improve_count = 0
            print(f" {best_score:.1f}/10 (+{improvement:.1f}) [{dt:.0f}s] — {gen_best_strategy}")
        else:
            no_improve_count += 1
            print(f" {best_score:.1f}/10 (=) [{dt:.0f}s]")

        history.append({
            "gen": gen,
            "score": gen_best_score,
            "summary": gen_best_strategy
        })

        # check target
        if target and best_score >= target:
            print(f"\n  STOP: target score {target} reached ({best_score:.1f})")
            break

        # check patience
        if patience and no_improve_count >= patience:
            print(f"\n  STOP: no improvement for {patience} generations (plateau)")
            break

    # write output
    out_path = seed_path.replace('.', '_evolved.', 1)
    if out_path == seed_path:
        out_path = seed_path + ".evolved"
    Path(out_path).write_text(best_code)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"  DONE in {elapsed:.0f}s — final score: {best_score:.1f}/10")
    print(f"  evolved file: {out_path}")
    print(f"{'='*60}\n")
    return best_code, best_score

if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="autoevolve — evolve any code artifact using LLM-powered natural selection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # evolve a sorting algorithm (with benchmark)
  python3 autoevolve.py seed.py evolve.md -b "python3 bench.py {file}"

  # evolve a prompt (no benchmark, just LLM judging)
  python3 autoevolve.py prompt.txt criteria.md --target 9.0

  # use codex, stop after 5 min or plateau
  python3 autoevolve.py app.py evolve.md -e codex --timeout 300 --patience 3
"""
    )
    p.add_argument("seed", help="path to the seed file to evolve")
    p.add_argument("criteria", help="path to criteria markdown with fitness definition")
    p.add_argument("-g", "--generations", type=int, default=10, help="max generations (default: 10)")
    p.add_argument("-n", "--population", type=int, default=3, help="mutations per generation (default: 3)")
    p.add_argument("-b", "--bench", type=str, default=None, help="benchmark command ({file} = temp file path)")
    p.add_argument("-e", "--engine", type=str, default="claude", choices=["claude", "codex"],
                   help="LLM engine: claude or codex (default: claude)")
    # stopping conditions
    p.add_argument("--target", type=float, default=None, help="stop when score reaches this value")
    p.add_argument("--patience", type=int, default=None, help="stop after N generations with no improvement")
    p.add_argument("--timeout", type=int, default=None, help="stop after N seconds total")
    p.add_argument("--reasoning", type=str, default="medium", choices=["low", "medium", "high"],
                   help="codex reasoning effort: low, medium, high (default: medium)")
    args = p.parse_args()
    evolve(args.seed, args.criteria, args.generations, args.population, args.bench,
           args.engine, args.target, args.patience, args.timeout, args.reasoning)
