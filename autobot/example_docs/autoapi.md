# AutoAPI

AutoAPI turns any website into a REST API with one command.

## Usage
```bash
# Turn a website into an API
python3 autoapi.py https://news.ycombinator.com

# Custom output directory
python3 autoapi.py https://example.com -o my_api

# Use codex
python3 autoapi.py https://example.com -e codex --reasoning low
```

## How it works
1. Fetches the page HTML
2. LLM analyzes the structure and plans REST endpoints
3. AI agent builds a FastAPI scraper + server
4. Tests that endpoints return valid JSON
5. Retries up to 3x if broken

## Output
A working API server you start with:
```bash
cd api_output/ && pip install -r requirements.txt && python3 main.py
```
Then hit http://localhost:8000/ for endpoint documentation.

## Best for
- Data-heavy sites: job boards, product catalogs, news sites, directories
- Sites without official APIs
- Quick data extraction projects
