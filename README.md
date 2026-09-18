# fantasy-football-manager

A weekly fantasy football lineup check that runs on GitHub Actions and
posts its report as a GitHub Issue — no server, no hosting, no
third-party email service. It flags bye weeks and injury tags on your
starters, catches bench players who outproject a starter in an eligible
slot, suggests waiver-wire upgrades, checks weather for outdoor games,
and (optionally) generates plain-language reasoning for its suggestions
via a free Groq API key.

Two platform variants are included, each fully self-contained in its
own folder:

- **[`espn/`](espn/README.md)** — for ESPN Fantasy leagues, via the
  `espn_api` Python library. Needs your league/team ID and a couple of
  session cookies.
- **[`sleeper/`](sleeper/README.md)** — for Sleeper leagues, via
  Sleeper's public API. Needs only your league ID and username — no
  login required. See that folder's README for how it differs from the
  ESPN version (Sleeper's API doesn't publish real weekly projections,
  ownership percentages, or defense-vs-position difficulty ranks, so a
  few features are adapted or dropped there).

## Quick start

1. Use this repo as a template (or fork it).
2. Open whichever folder matches your platform and follow its README.
3. Add the secrets it asks for (**Settings → Secrets and
   variables → Actions**).
4. Run the matching workflow once manually (**Actions** tab → pick the
   workflow → **Run workflow**) to confirm it works.
5. Uncomment the `schedule:` block in that workflow file to have it run
   automatically every Tuesday and Thursday during the season.

If you only play on one platform, feel free to delete the folder and
workflow file you don't need — the other one only runs via manual
dispatch by default (no schedule), so leaving both in place doesn't
cause anything to run on its own.

## Why GitHub Issues instead of email

Posting to a GitHub Issue means the workflow can authenticate with the
token GitHub already generates for every repo (`GITHUB_TOKEN`) — no
separate email service, API key, or account to set up for delivery.
Add collaborators to the repo and have them "Watch" it for GitHub's own
notification emails if you want the report to reach more than one
person.

## AI-generated reasoning (optional)

Both variants can generate 1-2 sentences of plain-language reasoning
for their bench-upgrade and waiver suggestions, via a free
[Groq](https://console.groq.com) API key — see either folder's README
for setup. This is entirely optional: without a key, both scripts fall
back to rule-based template reasoning, so a missing or invalid key
degrades sentence quality, never the report itself.

## License

MIT — see [LICENSE](LICENSE). Copy it, fork it, adapt it for your own
league.
