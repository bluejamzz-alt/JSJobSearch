# JSJobSearch

Automated job search for Singapore. Every morning it pulls fresh postings from
several job boards, grades each one against your requirements, drops anything
you have already seen, and emails you a ranked digest with links.

```
Tier A – strong match (3)
| # | Score        | Title                                | Company        | Salary            | Level   | Source          | Posted | Gaps               |
| 1 | 10/10 (100%) | Business Development Manager (Pay…)  | ACME PAYMENTS  | SGD 4,500-7,000/mo| manager | MyCareersFuture | 20 Sep | none               |
| 2 | 9/10 (90%)   | Account Executive – Merchant Solut…  | Global Pay     | SGD 4,000-6,500/mo| executive| JobStreet      | 22 Sep | Salary at least …  |
```

## How it works

1. **Search.** Each query in `config.yaml` runs against every enabled board.
2. **De-duplicate.** Same posting on two boards counts once (first board in config wins).
3. **Grade.** Every requirement is a pass/fail check. Score = requirements met ÷ total,
   shown as "7 of 10 requirements met (70%)".
4. **Tier.** A ≥ 80%, B ≥ 60%, C ≥ 40%. Below C is not sent. A failed *must-have*
   requirement (wrong title, excluded term, wrong country, blacklisted company) excludes the posting.
5. **Remember.** `data/seen_jobs.json` records what was sent, so each digest only has new postings.
6. **Deliver.** HTML email grouped by tier, with the Markdown digest attached. Reports are also
   saved to `reports/YYYY-MM-DD.md` and `.html`.

## Job boards

| Source | Needs an API key | Notes |
|---|---|---|
| MyCareersFuture | No | Public JSON API. Best Singapore coverage, includes salary and level. |
| JobStreet SG | No | JSON endpoint used by the website. Salary shown when the employer states it. |
| LinkedIn | No | Public listing pages. Detail pages give description, level and type; capped per run. |
| Adzuna | Free key | Turn on in `config.yaml` after adding `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`. |
| Jooble | Free key | Turn on after adding `JOOBLE_API_KEY`. |

These boards do not publish stable APIs. If a source starts returning 0 results,
its endpoint or field names probably changed: see *Troubleshooting*.

## Setup (about 10 minutes)

### 1. Install

```bash
git clone <this repo>
cd JSJobSearch
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Edit `config.yaml`

The file is commented. The parts you will most likely change:

- `search.queries`: the searches to run.
- `requirements`: the checks. Each has `values` (keywords / levels), optional `weight`,
  `must_have`, and `unknown` (what to do when the posting does not say: `met` or `unmet`).
- `grading.tiers`: the percentage thresholds.
- `requirements[id=company].values`: companies to skip, for example your current employer.

Check it with:

```bash
python -m jobsearch check-config
```

### 3. Email (Gmail)

1. Turn on 2-Step Verification on the Google account that will *send* the email.
2. Create an App Password: Google Account → Security → 2-Step Verification → App passwords.
3. Copy `.env.example` to `.env` and fill in `SMTP_USER`, `SMTP_PASSWORD` (the app password)
   and `EMAIL_TO`.

```bash
python -m jobsearch test-email
```

Any other SMTP provider works too: set `SMTP_HOST` and `SMTP_PORT` (465 for SSL, 587 for STARTTLS).

### 4. First run

```bash
python -m jobsearch run --dry-run      # search + grade, print the digest, send nothing
python -m jobsearch run                # search, grade, save reports, email, remember what was sent
```

Other flags: `--no-email`, `--include-seen`, `--reset-seen`, `--print`, `-v` for debug logs.

### 5. Run it every morning on GitHub Actions (no laptop needed)

1. Push this repo to GitHub.
2. Repository → Settings → Secrets and variables → Actions → *New repository secret* for each of:
   `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (and `SMTP_HOST`, `SMTP_PORT` if not Gmail).
   Optional: `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `ANTHROPIC_API_KEY`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Actions tab → *Daily job search* → *Run workflow* to test it once.

The workflow (`.github/workflows/daily-job-search.yml`) runs at 08:00 Singapore time daily,
emails the digest, and commits the report and the seen-jobs memory back to the repository.
Scheduled workflows only run from the default branch, so merge this to `main` first.

## Grading details

| Requirement type | What it checks |
|---|---|
| `title_keywords` | Any of the values appears in the title (whole words, so "intern" never matches "international"). |
| `description_keywords` | At least `min_matches` values appear in title or description. |
| `exclude_keywords` | None of the values appear (`scope: title` or `title_and_description`). |
| `salary_min` | Stated salary reaches `value` per month. Annual figures are divided by 12. `compare: max` passes if the top of the range reaches it, `compare: min` needs the bottom to. |
| `location` | Location mentions one of the values. |
| `work_arrangement` | On-site / hybrid / remote, from the board or inferred from the description. |
| `seniority` | Level inferred from title words (manager, senior, junior, director, VP…) or the board's label. |
| `employment_type` | Full time / permanent / contract as reported. |
| `posted_within_days` | Posting date is recent enough. |
| `company_exclude` | Company is not in the blacklist. |

Each requirement also supports:

- `weight` (default 1): counts more or less toward the percentage.
- `must_have: true`: failing it excludes the posting (or caps it at Tier C with
  `grading.must_have_failure: cap_c`).
- `unknown: met|unmet`: what to do when the posting does not state the information.
  Salary defaults to `unmet` (hidden salary costs a point, and the *Gaps* column says "salary not stated").

### Optional AI fit score

Set `ai_grading.enabled: true` and provide `ANTHROPIC_API_KEY`. Each shortlisted posting is then
read by Claude, which returns a 0-100 fit score and a one-line reason shown in the digest. The
final ranking blends it with the rule score (`weight: 0.3` = 30% AI). Set `affects_tier: true`
to let it move tiers. Only postings that already pass the rules are sent, so cost stays small.
The request opts into Anthropic's server-side refusal fallbacks so a declined request is
retried on a fallback model instead of failing.

### Optional Telegram push

Create a bot with @BotFather, get your chat id (message the bot, then open
`https://api.telegram.org/bot<TOKEN>/getUpdates`), set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`,
and turn on `delivery.telegram.enabled`. By default only Tier A goes to Telegram.

## Project layout

```
config.yaml                     your searches, requirements, tiers, delivery
jobsearch/
  pipeline.py                   search -> dedupe -> grade -> filter -> report -> deliver
  grader.py                     requirement checks, scoring, tiers
  sources/                      one file per job board (mycareersfuture, jobstreet, linkedin, adzuna, jooble)
  report.py                     Markdown / HTML / Telegram rendering
  notify/                       email (SMTP) and Telegram
  storage.py                    seen-jobs memory
  ai_grader.py                  optional Claude fit score
  __main__.py                   CLI
tests/                          offline tests with sample board responses in tests/fixtures/
.github/workflows/              daily-job-search.yml (schedule) and tests.yml (CI)
reports/                        daily digests
data/seen_jobs.json             what has been sent
```

## Adding a job board

Subclass `BaseSource` in `jobsearch/sources/`, implement `search(query) -> list[Job]` with a
pure `parse(payload)` static method, register it in `jobsearch/sources/__init__.py`, add a block
under `sources:` in `config.yaml`, and add a fixture plus a test in `tests/`.

## Troubleshooting

- **A source returns 0 results every run.** Run `python -m jobsearch run --dry-run -v` and look
  for `HTTP 4xx` lines. The board's endpoint or JSON shape probably changed; the endpoint is
  configurable under `sources.<name>.endpoint`, and the parser lives in `jobsearch/sources/<name>.py`.
- **LinkedIn returns HTTP 429.** You are being rate limited; lower `max_pages` / `max_details`
  or raise `delay_seconds`.
- **Email not sent.** `python -m jobsearch test-email` prints the exact SMTP error. Gmail needs
  an App Password, not your normal password.
- **Same jobs keep coming back.** Something is rewriting `data/seen_jobs.json`; on GitHub Actions
  make sure the workflow's commit step is pushing successfully.
- **Too many / too few results.** Tune `grading.tiers`, `min_tier_to_report`, or add keywords to
  `title_keywords` / `exclude_keywords`.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
