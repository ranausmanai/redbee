# Fitness Criteria: Landing Page Lighthouse Optimization

## Goal
Evolve a landing page HTML file to achieve the highest possible Google Lighthouse scores across all four categories: Performance, Accessibility, Best Practices, and SEO.

## Constraints
- Must be a single self-contained HTML file (inline CSS and JS, no external files)
- Must still look like a real, professional landing page — not a blank page or minimal HTML that games the scores
- Must have: a hero section with headline + CTA button, at least 3 feature cards, and a footer
- Must be visually appealing — good colors, spacing, typography, responsive layout
- No external resources (no CDN links, no external images, no Google Fonts URLs) — everything inline

## What "better" means (in priority order)
1. **Lighthouse Performance score** — fast load, no render-blocking, minimal DOM, efficient CSS
2. **Lighthouse Accessibility score** — proper ARIA labels, contrast ratios, semantic HTML, keyboard nav, alt text
3. **Lighthouse SEO score** — meta description, viewport tag, proper headings hierarchy, link text
4. **Lighthouse Best Practices score** — HTTPS-ready, no console errors, proper doctype, charset
5. **Visual quality** — must still look like a professional landing page, not a stripped-down skeleton

## Scoring Guide
- 0-2: page is broken, blank, or not valid HTML
- 3-4: basic page but major Lighthouse failures (missing viewport, no alt text, bad contrast)
- 5-6: decent page, Lighthouse scores around 60-75 across categories
- 7-8: good page, Lighthouse scores 80-90, most issues fixed
- 9-10: professional page with Lighthouse scores 95+ across all four categories
