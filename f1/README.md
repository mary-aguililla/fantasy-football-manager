# F1 Fantasy setup

This folder is self-contained — the script, its dependencies, and its
GitHub Actions workflow (`.github/workflows/f1-lineup-check.yml` at the
repo root) are already wired together. You don't need to create or move
any files, just add the secrets below.

**Read "Things that are provisional" near the bottom before trusting
every field this prints.** Unlike the ESPN/Sleeper versions, F1 Fantasy
has no public API and no community library, so this was built against
reverse-engineered endpoints — the core team-pull works, but a couple of
field names (budget, transfers remaining, DRS Boost) are best-effort
guesses until confirmed against a real run.

## 1. Get your F1 Fantasy session cookie

F1 Fantasy's real login endpoint is protected by Akamai bot detection
and is known to CAPTCHA scripted login attempts unpredictably — so
rather than automate a login (like the ESPN version doesn't either, for
the same reason), this reuses a session cookie from a browser where
you're already logged in:

1. Log into [fantasy.formula1.com](https://fantasy.formula1.com/en) in
   your browser, and go into the actual **team-picker app** (where you
   drag and drop drivers) — the cookie below is only set once you're in
   there, not just on the general logged-in homepage.
2. Open DevTools (right-click → Inspect) → **Application** tab (Chrome)
   or **Storage** tab (Firefox) → **Cookies** → `https://fantasy.formula1.com`.
3. Find the cookie named **`F1_FANTASY_007`** and copy its value.

This is a session credential — treat it like a password. **It's
also short-lived: decoding its JWT payload shows a 4-day expiry from
when it's issued** (`exp` minus `iat` = exactly 345,600 seconds), a lot
tighter than the ESPN version's ~1-year cookie. Practically, that means
this isn't a "set once for the season" secret — plan to repeat this step
every few days you want the check actively running, realistically
right before each race weekend rather than continuously. If the
workflow starts failing with an auth error, this is almost certainly
why — repeat this step to refresh it. It goes into a GitHub secret in
step 3 below, never into the code.

## 2. Get your league ID

Open your private league on fantasy.formula1.com and check the URL —
`leagueId=XXXXXXX`.

## 3. Add GitHub repo secrets

Delivery is a GitHub Issue posted in this repo, same as the other two
variants — no third-party email service to sign up for.

In your repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `F1_SESSION_COOKIE` | from step 1 |
| `F1_LEAGUE_ID` | from step 2 |
| `GROQ_API_KEY` | optional — see step 4 |

## 4. (Optional) Get a free Groq API key for AI-generated reasoning

Same setup as the ESPN version — see
[`../espn/README.md`](../espn/README.md#4-optional-get-a-free-groq-api-key-for-ai-generated-reasoning)
for the full walkthrough. Add the key as the `GROQ_API_KEY` secret above.

## 5. Test it, then turn on the schedule

Go to the **Actions** tab → "F1 Fantasy Team Check" → **Run workflow**.
Watch the run log — it prints the same report it posts, plus some
diagnostic lines (see below) — then check the **Issues** tab.

If it prints `No upcoming race found` or `more than N day(s) away`,
that's expected outside of a race weekend, not a bug — see "Why this
doesn't run weekly" below.

Once a real run works, edit `.github/workflows/f1-lineup-check.yml` and
uncomment the `schedule:` block so it checks in automatically every day.

## Why this doesn't run weekly

F1's calendar doesn't have a fixed cadence — some race weekends are
back-to-back, others are two or three weeks apart. Instead of a fixed
weekday cron, the workflow runs daily and the script itself decides
whether to actually post: it checks the real upcoming qualifying
deadline (via the free, public Jolpica-F1 API) and only builds a report
once that deadline is within `F1_DEADLINE_WINDOW_DAYS` (3 by default,
override via a `F1_DEADLINE_WINDOW_DAYS` secret or workflow env var). It
also checks for an already-open Issue with the same race name before
posting again, so a daily cron won't spam near-duplicate Issues across
that 3-day window.

## What it checks

- The upcoming Grand Prix, its circuit, and the actual qualifying
  deadline (F1 Fantasy locks your team at the start of qualifying, not
  race day) — pulled from Jolpica-F1, the free successor to the
  retired Ergast API.
- Weather at the circuit for both qualifying and race day, via
  Open-Meteo (free, no key) — flagged only for real outliers, same
  threshold as the ESPN/Sleeper versions.
- Your actual picked team (5 drivers + constructor) and prices, pulled
  from F1 Fantasy's own API using your session cookie.
- Budget remaining and transfers remaining, if those fields could be
  confidently identified on your account (see caveat below).
- A rule-based **DRS Boost suggestion** — whichever of your drivers had
  the best finish in the last race, with 1-2 sentences of AI-generated
  reasoning grounded in that result (falls back to a plain rule-based
  sentence if no Groq key is set).
- A link to your league's live leaderboard (see below for why it's a
  link, not a table).

## Things that are provisional

This was built entirely from a reverse-engineered auth flow and a thin,
incomplete community API wrapper — there's no official documentation and
no account was available to test against while building it. Specifically:

- **Budget and transfers-remaining fields**: the script scans your
  picked-team response for keys that merely *look* like a budget or
  transfer count (see `extract_team_meta()` in `lineup_check.py`) and
  shows them if found. It also unconditionally prints every top-level
  key it saw to the run log (`[team meta] top-level picked_team keys:
  ...`). If the guessed fields are missing or wrong in your first real
  run, check that log line and tell me (or edit the `BUDGET_KEY_HINTS`/
  `TRANSFER_KEY_HINTS` lists in the script) so it can be tightened.
- **DRS Boost detection**: same best-effort key-sniffing approach, on
  each picked player rather than the team as a whole.
- **League standings as a table**: F1 does publish a no-auth leaderboard
  feed (`fantasy.formula1.com/feeds/leaderboard/privateleague/...`), but
  it's scoped to cumulative team scores, not which drivers each manager
  picked — nothing to cross-reference against your team's data the way
  the ESPN/Sleeper standings tables do. Rather than build a
  half-useful table, the report just links to your league's live page.
- **Waiver-style transfer suggestions** (comparing your weakest driver
  against better-value alternatives at a similar price) aren't in this
  first version — the player pool and pricing plumbing is there
  (`get_player_pool()`), this just needs a value-ranking pass once the
  budget field above is confirmed to actually reflect what you can
  spend. Ask if you want this added once the basics are confirmed
  working.
- **`F1_FANTASY_API_VERSION`** (default `"2022"`): F1 Fantasy's API path
  is versioned independently of the season — the reference
  implementation this was built from used `2022` against live seasons
  well past that year. If a run ever fails with a 404 on every
  fantasy-api.formula1.com call, try bumping this.

None of these affect the calendar, weather, or qualifying-deadline logic
— those come from Jolpica-F1 and Open-Meteo, both stable public APIs
used as-is.
