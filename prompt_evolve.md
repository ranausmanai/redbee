# Fitness Criteria: Data Extraction Prompt

## Goal
Evolve the best possible prompt for extracting structured data from messy real-world text. The prompt will be given to an LLM along with raw text, and must produce clean, accurate JSON output.

## What the prompt must do
- Instruct an LLM to extract: name, email, phone, date, amount, currency, items/services, company, and address from arbitrary messy text
- Handle missing fields gracefully (null, not hallucinated)
- Output valid JSON every time — no markdown fences, no explanation, just JSON
- Work across different text types: invoices, emails, receipts, job offers, contracts

## What "better" means (in priority order)
1. **Accuracy** — extracts correct values, never hallucinated data. Missing fields should be null, not guessed.
2. **Consistency** — always produces the exact same JSON schema regardless of input
3. **Robustness** — handles weird formatting, typos, multi-language text, missing info
4. **Conciseness** — shorter prompts that work just as well score higher than verbose ones

## Scoring Guide
- 0-2: prompt doesn't produce JSON or hallucinates data
- 3-4: gets some fields right but inconsistent schema or misses obvious data
- 5-6: mostly correct, consistent schema, but fails on edge cases
- 7-8: accurate extraction, handles edge cases, clean schema, no hallucination
- 9-10: near-perfect — handles every test case, never hallucinates, minimal prompt length
