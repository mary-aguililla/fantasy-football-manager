# ESPN setup

This folder is self-contained — the script, its dependencies, and its
GitHub Actions workflow (`.github/workflows/espn-lineup-check.yml` at
the repo root) are already wired together. You don't need to create or
move any files, just add the secrets below.

## 1. Get your ESPN league ID and team ID
- **League ID**: log into fantasy.espn.com, open your league, look at
  the URL — `leagueId=XXXXXXX`.
- **Team ID**: click into your team, check the URL — `teamId=X`.

## 2. Get your espn_s2 and SWID cookies
1. While logged into fantasy.espn.com, open DevTools (right-click →
   Inspect).
2. Go to the **Application** tab (Chrome) or **Storage** tab (Firefox).
3. Under Cookies, click `https://fantasy.espn.com`.
4. Copy the full values for `espn_s2` and `SWID` (SWID includes the
   curly braces, e.g. `{ABC123...}`).

These are session credentials — treat them like a password. They go
into GitHub secrets in step 3, never into the code.

## 3. Add GitHub repo secrets
Delivery is a GitHub Issue posted in this repo, so there's no
third-party email service to sign up for — the workflow already
grants itself permission to open issues, and it authenticates with
the token GitHub generates automatically for every repo.

In your repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `ESPN_LEAGUE_ID` | from step 1 |
| `ESPN_TEAM_ID` | from step 1 |
| `ESPN_S2` | from step 2 |
| `ESPN_SWID` | from step 2 (include the `{ }`) |
| `GROQ_API_KEY` | optional — see step 4 |

## 4. (Optional) Get a free Groq API key for AI-generated reasoning
The bench-upgrade and waiver reasoning is AI-generated via **Groq**
(api.groq.com) — a fast-inference hosting company, unrelated to xAI's
"Grok" chatbot despite the near-identical name. This needs its own free
account, separate from GitHub:

1. Go to console.groq.com and sign up — no credit card required.
2. Create an API key from the console.
3. Add it as the `GROQ_API_KEY` secret in step 3's table.

The workflow's `AI_MODEL` is set to `openai/gpt-oss-120b`. If you want
a different one, `Search Models...` in the Groq Playground is the
authoritative source for what's actually available on your account —
avoid anything from the "Groq" publisher group (`compound`/
`compound-mini`, agentic models that reserve extra token budget for
tool use and can blow a free-tier limit on even a short prompt) and
anything whose name suggests a non-chat purpose (Whisper =
speech-to-text, Orpheus = text-to-speech, Prompt Guard/Safeguard =
classifiers).

If the key or model is missing or invalid, nothing breaks — the script
falls back to template-based reasoning, so a Groq hiccup degrades
sentence quality, never the report itself.

## 5. Test it, then turn on the schedule
Go to the **Actions** tab → "ESPN Lineup Check" → **Run workflow**.
Watch the run log — it prints the same report it posts — then check
the **Issues** tab for the new issue.

Once that works, edit `.github/workflows/espn-lineup-check.yml` and
uncomment the `schedule:` block at the top so it runs automatically
every Tuesday and Thursday.

If the run fails with a permissions error on the issue-creation step,
check the `permissions:` block in the yaml — GitHub treats an explicit
`permissions:` block as the complete list, so every scope the job needs
(`contents: read`, `issues: write`) has to be listed there by name.

## What it checks
- Your current record and standing, a simple strategy note (favor
  floor if you're ahead, favor upside if you're behind), and a
  playoff-picture line — all rule-based, not AI.
- Any **starter** on a bye or tagged Questionable/Doubtful/Out/IR, plus
  a matchup note if their opponent is a real outlier against that
  position, and a weather note for outdoor games with notably high
  wind or rain chance.
- Any **bench** player projected to outscore a starter in a slot
  they're eligible for (FLEX-aware), with 1-2 sentences of
  AI-generated reasoning grounded in real news headlines for both
  players — falls back to rule-based reasoning if the AI call fails.
- **Waiver suggestions** for every position on your roster, comparing
  your weakest player at each position against available free agents.
- **Bench options matching your situation** — volatile bench players
  surfaced when you're behind, consistent ones when you're ahead.
- **Widely-valued free agents** — high ownership and a real start rate,
  still on waivers.
- Full lineup tables (yours and your opponent's) and a league
  standings table.

Weather comes from Open-Meteo (free, no key). Player news comes from
ESPN's own public news API, filtered to headlines that actually mention
the player by name.

## One thing to remember
ESPN resets `espn_s2`/`SWID` roughly once a year, usually around draft
season (~August). When that happens the workflow starts failing (red
X's in the Actions tab) until you repeat step 2 and update the two
secrets.
