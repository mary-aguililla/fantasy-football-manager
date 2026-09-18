# Sleeper setup

This folder is self-contained — the script, its dependencies, and its
GitHub Actions workflow (`.github/workflows/sleeper-lineup-check.yml`
at the repo root) are already wired together. You don't need to create
or move any files, just add the secrets below.

**Read "Differences from the ESPN version" below before assuming this
does everything the ESPN version does.** Sleeper's public API doesn't
expose some of the data ESPN's does, so a few features are adapted or
dropped rather than ported one-for-one.

## 1. Get your Sleeper league ID and username
- **League ID**: open your league on sleeper.com (or in the app), look
  at the URL — `leagues/XXXXXXXXXXXXXXXXXX/...`. It's a long numeric ID.
- **Username**: your Sleeper login username (not your team name, not
  your display name if it differs) — the one you'd use at
  `sleeper.com/user/<username>`.

Unlike the ESPN version, that's it — Sleeper's league data is public
and readable with no login, so there's no cookie-grabbing step and no
`ESPN_S2`/`SWID` equivalent to manage.

**More than one league?** GitHub Issues (this script's delivery
mechanism) are per-repo, so if you're in multiple leagues and want a
separate notification stream for each, the simplest approach is one
copy of this repo per league, each with its own `SLEEPER_LEAGUE_ID`
secret — rather than trying to merge multiple leagues' reports into one
Issue thread.

## 2. Add GitHub repo secrets
In your repo: **Settings → Secrets and variables → Actions → New
repository secret**. Add each of these:

| Secret name | Value |
|---|---|
| `SLEEPER_LEAGUE_ID` | from step 1 |
| `SLEEPER_USERNAME` | from step 1 |
| `GROQ_API_KEY` | optional — see step 3 |

## 3. (Optional) Get a free Groq API key for AI-generated reasoning
Same setup as the ESPN version — see
[`../espn/README.md`](../espn/README.md#4-optional-get-a-free-groq-api-key-for-ai-generated-reasoning)
for the full walkthrough. Add the key as the `GROQ_API_KEY` secret in
step 2's table above.

## 4. Test it, then turn on the schedule
Go to the **Actions** tab → "Sleeper Lineup Check" → **Run workflow**.
Watch the run log — it prints the same report it posts — then check
the **Issues** tab.

Once that works, edit `.github/workflows/sleeper-lineup-check.yml` and
uncomment the `schedule:` block so it runs automatically every Tuesday
and Thursday.

If the projections endpoint isn't working for your league (see below),
the run won't fail — proj values will just show a `~` marker, silently
by design. Check the report for `~` markers to confirm which path ran.

## 5. Getting the report to other league members
Add them as collaborators (**Settings → Collaborators**), then have
each set their notification preference on the repo (top-right **Watch**
button → **All Activity**, or at least **Issues**) — being a
collaborator alone doesn't guarantee GitHub emails them, watching does.

---

## Differences from the ESPN version

Sleeper's API is public and needs no login, which is a real
simplification — but it also doesn't publish everything ESPN's
`espn_api` library does. Here's what changed, and why:

**Projected points (biggest change).** Sleeper has no *documented,
official* projections endpoint. This script tries an undocumented one
that Sleeper's own web app uses internally — it could change shape or
disappear without notice, since it's not a published API contract.
When it works, you get a real weekly projection, same as the ESPN
version. When it doesn't (or a specific player has no projection
posted), the script falls back to that player's **season average**
instead, and marks it with a `~` in the report (e.g. `14.2~`) so it's
never confused with a real projection. Season average is materially
weaker than a real projection — it doesn't know this week's opponent,
an injury return, or a role change — so treat `~` numbers with more
skepticism than plain ones, especially for bench-upgrade calls.

**Matchup difficulty.** The ESPN version flags "Tough vs DEN (2/32)"
using ESPN's defense-vs-position rank data. Sleeper's public API has no
equivalent, so this version's Matchup column is informational only
(just opponent + home/away, e.g. `@DEN`) — no difficulty judgment.

**"Widely valued" free agents.** The ESPN version uses ownership/start
percentages, which Sleeper doesn't publish. This version substitutes
Sleeper's trending-adds count (how many managers added a player
league-wide in the last 24 hours) — a momentum signal, not an ownership
snapshot. Different question, same rough intent.

**Player news.** Dropped entirely. ESPN's news API is keyed to ESPN's
own player IDs, which don't map cleanly onto Sleeper's player IDs —
rather than ship a feature that could silently mismatch players, this
version just doesn't include a news section.

**Playoff picture.** Sleeper doesn't publish "how many teams make the
playoffs" as a single settings field the way ESPN does. This version
estimates it at half your league's team count and says so explicitly
in the report, rather than stating a specific number with false
confidence.

Everything else — weather, the AI reasoning itself, bye/injury flags,
bench-upgrade logic, waiver suggestions, standings, and GitHub Issue
delivery — works the same way it does in the ESPN version.
