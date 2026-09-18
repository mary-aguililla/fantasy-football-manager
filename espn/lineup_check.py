#!/usr/bin/env python3
"""
Weekly ESPN fantasy football lineup check.

Runs every Tuesday morning via GitHub Actions during the NFL season.
Pulls this week's matchup, flags starters who are on bye or carry an
injury designation, flags bench players projected to outscore a
starter in an eligible slot, and opens a GitHub Issue in this repo
with the summary — no third-party email service required.

Also pulls (both free, no API key, no signup):
  - Weather for outdoor games, via Open-Meteo.
  - Recent player news, via ESPN's own public (non-fantasy) news API,
    filtered to headlines that actually mention the player by name —
    that endpoint sometimes returns generic weekly-roundup articles
    instead of player-specific news, so headlines that don't mention
    the player are skipped rather than shown out of context.

The report is built as Markdown (tables + sections), since GitHub
renders that directly in the Issue body — no monospace/code-fence
formatting fighting for space as more fields get added.

All config comes from environment variables (set as GitHub Actions
secrets — see the README for the full list and how to find each one).
"""

import os
from datetime import datetime, timezone
import requests
from espn_api.football import League

# ---- Config ----
LEAGUE_ID = int(os.environ["ESPN_LEAGUE_ID"])
TEAM_ID = int(os.environ["ESPN_TEAM_ID"])
YEAR = int(os.environ.get("LEAGUE_YEAR", "2026"))
ESPN_S2 = os.environ["ESPN_S2"]
SWID = os.environ["ESPN_SWID"]

GH_TOKEN = os.environ["GH_TOKEN"]
# GITHUB_REPOSITORY is auto-set by Actions on every run — "you/repo-name".
GH_REPOSITORY = os.environ["GITHUB_REPOSITORY"]

BENCH_SLOTS = {"BE", "IR"}
INJURY_FLAGS = {"QUESTIONABLE", "DOUBTFUL", "OUT", "INJURY_RESERVE", "SUSPENSION"}

# Static stadium reference: lat/lon + dome status, keyed by the same team
# abbreviations espn_api uses (its PRO_TEAM_MAP), so lookups from
# player.proTeam need no translation. Hardcoded deliberately — static,
# rarely-changing data that can't fail at runtime the way an API call
# could. Retractable-roof stadiums (ARI, ATL, DAL, HOU, IND) are marked
# dome: True since they're closed for most games; the only miss is a
# roof left open on a nice day, which just means no note instead of an
# unneeded one.
TEAM_STADIUMS = {
    "ARI": {"lat": 33.5276, "lon": -112.2626, "city": "Glendale, AZ", "dome": True},
    "ATL": {"lat": 33.7554, "lon": -84.4009, "city": "Atlanta, GA", "dome": True},
    "BAL": {"lat": 39.2780, "lon": -76.6227, "city": "Baltimore, MD", "dome": False},
    "BUF": {"lat": 42.7738, "lon": -78.7870, "city": "Orchard Park, NY", "dome": False},
    "CAR": {"lat": 35.2258, "lon": -80.8528, "city": "Charlotte, NC", "dome": False},
    "CHI": {"lat": 41.8623, "lon": -87.6167, "city": "Chicago, IL", "dome": False},
    "CIN": {"lat": 39.0954, "lon": -84.5160, "city": "Cincinnati, OH", "dome": False},
    "CLE": {"lat": 41.5061, "lon": -81.6995, "city": "Cleveland, OH", "dome": False},
    "DAL": {"lat": 32.7473, "lon": -97.0945, "city": "Arlington, TX", "dome": True},
    "DEN": {"lat": 39.7439, "lon": -105.0201, "city": "Denver, CO", "dome": False},
    "DET": {"lat": 42.3400, "lon": -83.0456, "city": "Detroit, MI", "dome": True},
    "GB": {"lat": 44.5013, "lon": -88.0622, "city": "Green Bay, WI", "dome": False},
    "HOU": {"lat": 29.6847, "lon": -95.4107, "city": "Houston, TX", "dome": True},
    "IND": {"lat": 39.7601, "lon": -86.1639, "city": "Indianapolis, IN", "dome": True},
    "JAX": {"lat": 30.3239, "lon": -81.6373, "city": "Jacksonville, FL", "dome": False},
    "KC": {"lat": 39.0489, "lon": -94.4839, "city": "Kansas City, MO", "dome": False},
    "LAC": {"lat": 33.9535, "lon": -118.3392, "city": "Inglewood, CA", "dome": True},
    "LAR": {"lat": 33.9535, "lon": -118.3392, "city": "Inglewood, CA", "dome": True},
    "LV": {"lat": 36.0909, "lon": -115.1833, "city": "Las Vegas, NV", "dome": True},
    "MIA": {"lat": 25.9580, "lon": -80.2389, "city": "Miami Gardens, FL", "dome": False},
    "MIN": {"lat": 44.9737, "lon": -93.2577, "city": "Minneapolis, MN", "dome": True},
    "NE": {"lat": 42.0909, "lon": -71.2643, "city": "Foxborough, MA", "dome": False},
    "NO": {"lat": 29.9511, "lon": -90.0812, "city": "New Orleans, LA", "dome": True},
    "NYG": {"lat": 40.8135, "lon": -74.0745, "city": "East Rutherford, NJ", "dome": False},
    "NYJ": {"lat": 40.8135, "lon": -74.0745, "city": "East Rutherford, NJ", "dome": False},
    "PHI": {"lat": 39.9008, "lon": -75.1675, "city": "Philadelphia, PA", "dome": False},
    "PIT": {"lat": 40.4468, "lon": -80.0158, "city": "Pittsburgh, PA", "dome": False},
    "SEA": {"lat": 47.5952, "lon": -122.3316, "city": "Seattle, WA", "dome": False},
    "SF": {"lat": 37.4033, "lon": -121.9694, "city": "Santa Clara, CA", "dome": False},
    "TB": {"lat": 27.9759, "lon": -82.5033, "city": "Tampa, FL", "dome": False},
    "TEN": {"lat": 36.1665, "lon": -86.7713, "city": "Nashville, TN", "dome": False},
    "WSH": {"lat": 38.9077, "lon": -76.8645, "city": "Landover, MD", "dome": False},
}

WEATHER_WIND_FLAG_MPH = 20
WEATHER_PRECIP_FLAG_PCT = 50
NEWS_STALE_AFTER_DAYS = 5
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_weather_cache = {}
_news_cache = {}

# Groq config (api.groq.com) — chosen after GitHub Models turned out to be
# fully retired as of July 30, 2026, which wasn't known when that version
# was first built. openai/gpt-oss-120b is confirmed available on the account this was built against
# account (a Playground screenshot, not a guess) with an 8K TPM free-tier
# budget per Groq's own rate-limits docs — comfortably covers this
# script's short prompts. groq/compound was tried first and failed: its
# agentic tool-reservation overhead blew the token budget even on a short
# request (413 request_too_large). This script sends a plain request with
# no tools attached, which avoids that failure mode.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")


def get_my_team(league):
    for team in league.teams:
        if team.team_id == TEAM_ID:
            return team
    raise SystemExit(
        f"Team ID {TEAM_ID} not found in league {LEAGUE_ID}. "
        f"Known team IDs: {[t.team_id for t in league.teams]}"
    )


def get_my_box_score(league, week):
    for bs in league.box_scores(week):
        if (bs.home_team and bs.home_team.team_id == TEAM_ID) or (
            bs.away_team and bs.away_team.team_id == TEAM_ID
        ):
            return bs
    return None


def upgrade_reasoning(bench_proj, starter_proj, bench_matchup, starter_matchup):
    """1-2 template sentences explaining a bench-upgrade suggestion \u2014
    how big the projection gap actually is, and whether the matchup
    data agrees with or complicates the call. Rule-based logic over
    numbers already computed here, not AI reasoning."""
    gap = bench_proj - starter_proj
    sentences = []
    if gap >= 3:
        sentences.append(f"That's a clear enough gap ({gap:.1f} pts) to be a fairly confident swap.")
    else:
        sentences.append(f"The gap is thin ({gap:.1f} pts), so this one's closer than it looks.")

    if bench_matchup.startswith("Tough") and starter_matchup.startswith("Favorable"):
        sentences.append(
            "Worth noting: the matchup actually favors sticking with your current starter, "
            "which cuts against the projection edge."
        )
    elif bench_matchup.startswith("Favorable") and starter_matchup.startswith("Tough"):
        sentences.append("The matchup backs up the swap too \u2014 the bench option also draws the easier defense.")
    return " ".join(sentences)


def call_ai_model(prompt):
    """Calls Groq's chat completions endpoint (OpenAI-compatible), as a
    plain chat request \u2014 no tool use. groq/compound (tools enabled) hit
    a real free-tier TPM ceiling in testing: 413 request_too_large, which
    on Groq means the model's own context/tool-reservation overhead blew
    the token budget, not that the prompt itself was large. Dropping
    tool use avoids that overhead entirely. Needs its own GROQ_API_KEY
    secret (free, no credit card at signup) \u2014 separate account from
    GitHub, not reusing GH_TOKEN. Returns the response text, or None on
    any failure \u2014 this must never be what breaks a run."""
    if not GROQ_API_KEY:
        print("AI reasoning skipped: GROQ_API_KEY is not set.")
        return None
    if not AI_MODEL:
        print(
            "AI reasoning skipped: AI_MODEL is not set. Get a plain "
            "(non-compound) model name from console.groq.com and set it "
            "in the workflow's env: block."
        )
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                # Bumped from 300 — gpt-oss-120b is a reasoning model and can
                # spend tokens on internal reasoning before the final answer;
                # 300 may have been too tight, cutting off the answer while
                # leaving no error (see the empty-content check below).
                "max_completion_tokens": 800,
                "stream": False,
            },
            timeout=30,
        )
        if not resp.ok:
            # Print Groq's actual error body (e.g. bad key, bad model name,
            # rate limit) so it shows up in the Actions run log — the
            # report itself still falls back silently, but this run's
            # log will say exactly why.
            print(f"AI reasoning call failed ({resp.status_code}): {resp.text[:500]}")
            return None
        choice = resp.json()["choices"][0]
        content = choice["message"]["content"].strip()
        if not content:
            # A 200 OK with empty content is a different failure than an
            # HTTP error — nothing above would have caught this. Logging
            # finish_reason specifically: "length" confirms the response
            # was cut off before finishing (the reasoning-budget theory),
            # anything else points somewhere new.
            print(
                f"AI reasoning call returned empty content. "
                f"finish_reason={choice.get('finish_reason')!r}"
            )
            return None
        return content
    except Exception as e:
        print(f"AI reasoning call failed with an unexpected error: {e}")
        return None


def ai_upgrade_reasoning(bench_player, starter):
    """AI-generated reasoning for a bench-upgrade suggestion, grounded in
    the news headlines already being pulled for both players — not just
    the numbers. No live web search (see call_ai_model \u2014 that hit a
    real free-tier ceiling on Groq's agentic model). Explicitly
    instructed not to invent anything beyond what's given. Falls back to
    the rule-based upgrade_reasoning() if the AI call fails for any
    reason, so a Groq hiccup degrades sentence quality, not the report
    itself."""
    bench_matchup = matchup_cell(bench_player)
    starter_matchup = matchup_cell(starter)
    bench_news = get_player_news(bench_player)
    starter_news = get_player_news(starter)

    facts = [
        f"Bench option: {bench_player.name} ({bench_player.position}), "
        f"projected {bench_player.projected_points:.1f} points. "
        f"Matchup: {bench_matchup or 'no notable matchup data'}.",
        f"News on {bench_player.name}: {bench_news[0][0] if bench_news else 'none found in the last few days'}.",
        f"Current starter: {starter.name} ({starter.position}, {starter.slot_position}), "
        f"projected {starter.projected_points:.1f} points. "
        f"Matchup: {starter_matchup or 'no notable matchup data'}.",
        f"News on {starter.name}: {starter_news[0][0] if starter_news else 'none found in the last few days'}.",
    ]
    prompt = (
        "You're helping someone decide on a fantasy football lineup swap. "
        "Using ONLY the facts below \u2014 don't invent or assume anything not "
        "stated here \u2014 write 1-2 concise, plain-spoken sentences on whether "
        "this swap makes sense.\n\n" + "\n".join(facts)
    )
    result = call_ai_model(prompt)
    if result:
        return result
    return upgrade_reasoning(
        bench_player.projected_points, starter.projected_points, bench_matchup, starter_matchup
    )


def ai_availability_reasoning(player):
    """AI-generated one-sentence take on why a free agent might have
    low ownership/start rate despite the stats shown.

    Deliberately does NOT include this week's matchup difficulty as a
    fact here, even though matchup_cell() is used elsewhere in the
    report. Season-long ownership is driven by role/usage/scheme
    factors, not a single week's matchup — including matchup as one of
    only a few facts made the model reach for it as the explanation
    just because it was the most concrete quantified fact on hand, even
    though weekly matchup has nothing to do with why someone's been
    under-owned all season. Confirmed this was happening in practice
    before removing it.

    Pulls up to 3 headlines from a 30-day window, not the usual 5-day
    one \u2014 a single recent headline is often just a game recap
    ("completed X of Y passes"), which has nothing substantive to say
    about a role or scheme issue. A 5-day cutoff also actively works
    against this question: the kind of season-outlook piece that
    actually explains chronic low ownership is often more than 5 days
    old, and discarding it for staleness defeats the purpose here \u2014
    unlike a caller checking "is there a new injury," where a 5-day
    cutoff is exactly right. This one intentionally trades recency for
    relevance.

    Explicitly instructed to focus on role/usage over matchup, and to
    say plainly when nothing in the given facts actually explains it,
    rather than fabricate a plausible-sounding reason. Returns '' (not
    a fallback sentence) if the AI call fails \u2014 this is supplementary
    context, not a suggestion, so it's fine for it to simply not appear
    rather than show a lesser rule-based guess."""
    note = availability_note(player)
    news_items = get_player_news(player, max_age_days=30)[:3]
    news_text = (
        " | ".join(headline for headline, _ in news_items)
        if news_items else "none found in the last few days"
    )
    facts = [
        f"Player: {player.name} ({player.position}). "
        f"Owned {getattr(player, 'percent_owned', -1):.0f}%, "
        f"started {getattr(player, 'percent_started', -1):.0f}% of the time this week, "
        f"projected {player.projected_points:.1f} points.",
        f"Availability flag: {note or 'none \u2014 appears active and healthy'}.",
        f"Recent headlines: {news_text}.",
    ]
    prompt = (
        "You're helping a fantasy football manager understand why a "
        "free agent has low ownership or a low start rate despite "
        "decent-looking stats \u2014 this is almost always a role, usage, or "
        "team-context issue (offensive scheme, target/carry share, "
        "depth chart, supporting cast), NOT this week's opponent, so "
        "don't cite matchup difficulty as the reason. Using ONLY the "
        "facts below, write ONE concise sentence on what's likely "
        "limiting this player's value. If nothing here actually "
        "explains it, say plainly that there's no clear reason in the "
        "available data \u2014 don't invent one.\n\n" + "\n".join(facts)
    )
    return call_ai_model(prompt) or ""


def availability_note(player):
    """Short flag explaining why a free agent might be available despite
    decent stats — an injury designation, a bye week, or otherwise
    inactive this week. Uses injuryStatus/active_status, which espn_api
    already populates on every Player object including free agents \u2014
    no extra API call. '' when there's no red flag."""
    status = (getattr(player, "injuryStatus", None) or "").upper()
    if status in INJURY_FLAGS:
        return status.title()
    active_status = getattr(player, "active_status", "")
    if active_status == "bye":
        return "Bye week"
    if active_status == "inactive":
        return "Inactive this week"
    return ""


def matchup_cell(player):
    """'Tough vs DEN (2/32)' / 'Favorable vs DET (31/32)' / '' — using
    ESPN's own opponent-rank data espn_api already pulls per player, no
    extra call. Rank 1 = toughest defense against that position, 32 =
    easiest. Only real outliers (<=10 or >=23) get a note."""
    rank = getattr(player, "pro_pos_rank", 0)
    opp = getattr(player, "pro_opponent", "None")
    if not rank or not opp or opp == "None":
        return ""
    if rank <= 10:
        return f"Tough vs {opp} ({rank}/32)"
    if rank >= 23:
        return f"Favorable vs {opp} ({rank}/32)"
    return ""


def get_week_schedule(year, week):
    """One call for a week's NFL slate: opponent + home/away per team.
    Returns {team_abbr: {'opponent': abbr, 'home': bool}}. A team on
    bye that week is simply absent from the result. Best-effort —
    returns {} on any hiccup, which just means weather/lookahead notes
    go blank for that week, not a crash. Reused for both this week's
    weather check and future weeks' schedule lookahead."""
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
            params={"dates": year, "seasontype": 2, "week": week},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for event in data.get("events", []):
            for competition in event.get("competitions", []):
                competitors = competition.get("competitors", [])
                if len(competitors) != 2:
                    continue
                for i, comp in enumerate(competitors):
                    abbr = comp.get("team", {}).get("abbreviation")
                    home_away = comp.get("homeAway")
                    opp_abbr = competitors[1 - i].get("team", {}).get("abbreviation")
                    if abbr and opp_abbr:
                        result[abbr] = {"opponent": opp_abbr, "home": home_away == "home"}
        return result
    except Exception:
        return {}


def fetch_weather(lat, lon, date):
    """Daily max wind + precip probability via Open-Meteo (free, no
    key). Cached per (location, date) so players in the same game only
    trigger one call."""
    key = (round(lat, 2), round(lon, 2), date.isoformat())
    if key in _weather_cache:
        return _weather_cache[key]
    try:
        resp = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "precipitation_probability_max,wind_speed_10m_max",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "auto",
                "start_date": date.isoformat(),
                "end_date": date.isoformat(),
            },
            timeout=15,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily", {})
        wind_list = daily.get("wind_speed_10m_max") or []
        precip_list = daily.get("precipitation_probability_max") or []
        result = (
            wind_list[0] if wind_list else None,
            precip_list[0] if precip_list else None,
        )
    except Exception:
        result = None
    _weather_cache[key] = result
    return result


def weather_cell(player, week_schedule):
    """'\u26a0\ufe0f 74% rain, Denver, CO' / '' — outdoor games only, only when
    wind or rain chance is a real outlier."""
    team = player.proTeam
    stadium = TEAM_STADIUMS.get(team)
    if not stadium or stadium["dome"]:
        return ""
    if not player.pro_opponent or player.pro_opponent == "None":
        return ""

    entry = week_schedule.get(team)
    if entry is None:
        return ""
    venue_abbr = team if entry["home"] else player.pro_opponent
    venue = TEAM_STADIUMS.get(venue_abbr)
    if not venue or venue["dome"]:
        return ""

    game_date = getattr(player, "game_date", None)
    if not game_date:
        return ""

    forecast = fetch_weather(venue["lat"], venue["lon"], game_date.date())
    if not forecast:
        return ""
    wind, precip = forecast

    flags = []
    if wind is not None and wind >= WEATHER_WIND_FLAG_MPH:
        flags.append(f"{wind:.0f}mph wind")
    if precip is not None and precip >= WEATHER_PRECIP_FLAG_PCT:
        flags.append(f"{precip:.0f}% rain")
    if not flags:
        return ""
    return f"\u26a0\ufe0f {', '.join(flags)}, {venue['city']}"


def trend_cell(player, current_week, lookback=3):
    """'\U0001F4C8 18.2 last 2wk vs 9.4 szn' / '\U0001F4C9 ...' / '' —
    only flags a real outlier (>=20% deviation from season average),
    and only once there's at least one completed week of history to
    compare against. Uses player.stats, which espn_api already pulls —
    no extra API call. Early season, this will mostly be blank; that's
    expected, not a bug — there isn't enough history yet to trend."""
    weeks = [
        w for w in range(max(1, current_week - lookback), current_week)
        if w in player.stats and player.stats[w].get("points") is not None
    ]
    if not weeks:
        return ""
    recent_avg = sum(player.stats[w]["points"] for w in weeks) / len(weeks)
    season_avg = getattr(player, "avg_points", 0)
    if not season_avg:
        return ""
    diff = (recent_avg - season_avg) / season_avg
    if diff >= 0.20:
        return f"\U0001F4C8 {recent_avg:.1f} last {len(weeks)}wk vs {season_avg:.1f} szn"
    if diff <= -0.20:
        return f"\U0001F4C9 {recent_avg:.1f} last {len(weeks)}wk vs {season_avg:.1f} szn"
    return ""


def strategy_lean(team):
    """Returns ('ahead'|'behind'|None, note_text) — a simple
    win/loss threshold, not AI reasoning. None/'' when roughly .500,
    since there's no strong lean either way. The direction is reused
    to filter which bench volatility notes actually get surfaced."""
    if team.wins > team.losses:
        return "ahead", "You're ahead in the standings — favor the safer, more consistent option on close start/sit calls."
    if team.losses > team.wins:
        return "behind", "You're behind in the standings — a week to lean toward upside over floor on close calls."
    return None, ""


def volatility_cell(player, current_week, lookback=5):
    """'\U0001F3B2 High variance' / '\U0001F6E1\ufe0f Consistent' / '' — based on
    the spread of actual weekly points over up to the last 5 completed
    weeks (coefficient of variation: stdev/mean, which normalizes for
    players who simply score more in general). Needs at least 3 data
    points to be meaningful; blank otherwise, same as trend_cell."""
    weeks = [
        w for w in range(max(1, current_week - lookback), current_week)
        if w in player.stats and player.stats[w].get("points") is not None
    ]
    if len(weeks) < 3:
        return ""
    pts = [player.stats[w]["points"] for w in weeks]
    mean = sum(pts) / len(pts)
    if mean <= 0:
        return ""
    variance = sum((x - mean) ** 2 for x in pts) / len(pts)
    cv = (variance ** 0.5) / mean
    if cv >= 0.6:
        return "\U0001F3B2 High variance"
    if cv <= 0.25:
        return "\U0001F6E1\ufe0f Consistent"
    return ""


def playoff_picture_note(league, team):
    """One line on playoff standing, using league.settings —
    playoff_team_count and reg_season_count are stable base-class
    fields (unlike position_slot_counts, which I've deliberately
    avoided elsewhere for being fragile)."""
    try:
        spots = league.settings.playoff_team_count
        total_weeks = league.settings.reg_season_count
    except Exception:
        return ""
    weeks_left = max(0, total_weeks - league.current_week)
    if team.standing <= spots:
        return f"In a playoff spot (#{team.standing} of {spots}), {weeks_left} weeks left in the regular season."
    out_by = team.standing - spots
    return f"Outside the playoffs by {out_by} spot(s) (#{team.standing}, {spots} spots available), {weeks_left} weeks left."


def league_wide_value_adds(league, week, roster_positions, limit=5):
    """Free agents with unusually high ownership AND a meaningful start
    rate relative to typical waiver fare — a 'widely valued, still
    available' signal, NOT a week-over-week trend (the library only
    exposes a current snapshot, no ownership-change data). Scoped to
    positions you actually roster, across the whole position group, not
    just where you're weak.

    Originally this only filtered on percent_owned, which let through
    players like a 46%-owned WR projected for 0.0 points with a 1% start
    rate — high ownership left over from earlier in the season, but
    nobody actually playing them now. Fixed: now also requires a
    positive projection and a real start rate, and surfaces the same
    injury/bye/inactive context waiver_suggestions shows, so a "why is
    this player actually available" question has an answer right in the
    report instead of needing to be asked."""
    lines = []
    for pos in sorted(roster_positions):
        try:
            candidates = league.free_agents(week=week, size=15, position=pos)
        except Exception:
            continue
        notable = [
            c for c in candidates
            if getattr(c, "percent_owned", -1) >= 30
            and getattr(c, "percent_started", -1) >= 5
            and c.projected_points > 0
        ]
        notable.sort(key=lambda c: c.percent_owned, reverse=True)
        for c in notable[:2]:
            bits = [f"{c.percent_owned:.0f}% owned", f"{c.percent_started:.0f}% started",
                    f"proj {c.projected_points:.1f}"]
            note = availability_note(c)
            if note:
                bits.append(f"\u26a0\ufe0f {note}")
            line = f"- **{c.name}** ({pos}) \u2014 {' \u00b7 '.join(bits)}"
            context = ai_availability_reasoning(c)
            if context:
                line += f"\n  {context}"
            lines.append(line)
    return lines[:limit]


def waiver_suggestions(league, week, roster):
    """For each position on your roster (excluding IR), compares your
    weakest rostered player at that position against available free
    agents — independent of injury/bye status. Surfaces the clearest
    upgrade per position, if any exists. Best-effort per position: a
    failure on one just skips it, not the whole section."""
    by_position = {}
    for p in roster:
        if p.slot_position == "IR":
            continue
        by_position.setdefault(p.position, []).append(p)

    lines = []
    for pos in sorted(by_position):
        worst = min(by_position[pos], key=lambda p: p.projected_points)
        try:
            candidates = league.free_agents(week=week, size=5, position=pos)
        except Exception:
            continue
        for c in candidates:
            if c.projected_points > worst.projected_points:
                bits = [f"proj {c.projected_points:.1f}"]
                if getattr(c, "percent_owned", -1) >= 0:
                    bits.append(f"{c.percent_owned:.0f}% owned")
                note = availability_note(c)
                if note:
                    bits.append(f"\u26a0\ufe0f {note}")
                line = (
                    f"- **{c.name}** ({pos}) \u2014 {' \u00b7 '.join(bits)} "
                    f"\u2192 outprojects your **{worst.name}** (proj {worst.projected_points:.1f})"
                )
                context = ai_availability_reasoning(c)
                if context:
                    line += f"\n  {context}"
                lines.append(line)
                break  # clearest upgrade per position, not every candidate
    return lines


def _last_name(full_name):
    parts = full_name.replace(".", "").split()
    while len(parts) > 1 and parts[-1].lower() in NAME_SUFFIXES:
        parts.pop()
    return parts[-1] if parts else full_name


def _extract_url(item):
    links = item.get("links")
    if isinstance(links, dict):
        web = links.get("web")
        if isinstance(web, dict) and web.get("href"):
            return web["href"]
    if isinstance(links, list):
        for link in links:
            if isinstance(link, dict) and link.get("href"):
                return link["href"]
    return item.get("link") or item.get("url")


def get_player_news(player, max_age_days=NEWS_STALE_AFTER_DAYS):
    """All headlines that actually mention this player by last name,
    published within max_age_days, most recent first. Returns a list
    of (headline, url_or_None) tuples \u2014 empty list if none. The
    player-news endpoint sometimes returns generic roundup articles
    that just happen to reference the player — the name check filters
    those out rather than showing them out of context. Best-effort: any
    parsing hiccup returns an empty list quietly.

    max_age_days is a parameter, not hardcoded, because different
    callers want different windows: "recent news for a player you
    already started" wants tight recency (the default, 5 days) \u2014 an
    old injury update is actively misleading. "Why is this free agent
    generally undervalued" wants season-long context instead \u2014 a
    perfectly relevant outlook piece from 2-3 weeks ago would be wrongly
    discarded by a 5-day cutoff built for the other question.
    ai_availability_reasoning passes a much wider window for this
    reason.

    Cached per (player, max_age_days) — a player asked about at two
    different windows triggers two fetches rather than one, but at this
    script's scale that's a non-issue and it keeps the caching simple.
    Pulls up to 10 candidates from ESPN so there's a real chance of
    hitting something more substantive than a single game recap."""
    cache_key = (player.playerId, max_age_days)
    if cache_key in _news_cache:
        return _news_cache[cache_key]
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players",
            params={"playerId": player.playerId, "limit": 10},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("feed") or data.get("items") or data.get("news") or []
        last_name = _last_name(player.name).lower()

        result = []
        for item in items:
            headline = item.get("headline") or item.get("title")
            if not headline or last_name not in headline.lower():
                continue
            published_raw = item.get("published") or item.get("lastModified")
            if published_raw:
                try:
                    published = datetime.fromisoformat(str(published_raw).replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - published).days > max_age_days:
                        continue
                except Exception:
                    pass
            result.append((headline, _extract_url(item)))
        _news_cache[cache_key] = result
        return result
    except Exception:
        return []


def league_standings_table(league, my_team_id):
    """Markdown table of the full league, ordered by standing \u2014 record
    and points for/against per team. Uses league.teams, which is
    already fetched as part of the normal League() init \u2014 no extra
    API call. Your own team's name is bolded so it's easy to find in a
    14-team list."""
    lines = ["| # | Team | Record | PF | PA |", "|---|---|---|---|---|"]
    for team in sorted(league.teams, key=lambda t: t.standing):
        record = f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else "")
        name = f"**{team.team_name}**" if team.team_id == my_team_id else team.team_name
        lines.append(f"| {team.standing} | {name} | {record} | {team.points_for:.1f} | {team.points_against:.1f} |")
    return "\n".join(lines)


def lineup_table(players, with_notes, week_schedule=None, current_week=None):
    """Markdown table for a lineup. with_notes=True adds Matchup/Weather/
    Trend columns (used for your lineup only, not the opponent's)."""
    if with_notes:
        lines = ["| Slot | Player | Pos | Proj | Matchup | Weather | Trend |",
                 "|---|---|---|---|---|---|---|"]
    else:
        lines = ["| Slot | Player | Pos | Proj |", "|---|---|---|---|"]

    for p in sorted(players, key=lambda x: x.slot_position):
        row = f"| {p.slot_position} | {p.name} | {p.position} | {p.projected_points:.1f}"
        if with_notes:
            row += (
                f" | {matchup_cell(p)} | {weather_cell(p, week_schedule)} "
                f"| {trend_cell(p, current_week)}"
            )
        row += " |"
        lines.append(row)
    return "\n".join(lines)


def build_report(league, team, week_schedule):
    week = league.current_week
    bs = get_my_box_score(league, week)

    if bs is None:
        return f"No matchup found for Week {week} \u2014 league bye week, or check ESPN_TEAM_ID."

    if bs.home_team.team_id == TEAM_ID:
        my_lineup, opp_lineup = bs.home_lineup, bs.away_lineup
        my_projected = bs.home_projected
        opponent, opp_projected = bs.away_team, bs.away_projected
    else:
        my_lineup, opp_lineup = bs.away_lineup, bs.home_lineup
        my_projected = bs.away_projected
        opponent, opp_projected = bs.home_team, bs.home_projected

    starters = [p for p in my_lineup if p.slot_position not in BENCH_SLOTS]
    bench = [p for p in my_lineup if p.slot_position in BENCH_SLOTS]
    opp_starters = [p for p in opp_lineup if p.slot_position not in BENCH_SLOTS]

    opponent_name = opponent.team_name if opponent else "a bye"
    record = f"{team.wins}-{team.losses}" + (f"-{team.ties}" if team.ties else "")

    parts = [
        f"## Week {week} \u2014 {team.team_name} vs {opponent_name}",
        f"**Record:** {record} (#{team.standing} of {len(league.teams)})  "
        f"**Projected:** {my_projected:.1f} vs {opp_projected:.1f}",
    ]
    lean, strategy_text = strategy_lean(team)
    if strategy_text:
        parts.append(f"*{strategy_text}*")
    playoff_line = playoff_picture_note(league, team)
    if playoff_line:
        parts.append(f"*{playoff_line}*")
    parts.append("")

    # Bye / injury flags
    problems = []
    for p in starters:
        status = (p.injuryStatus or "").upper()
        if p.on_bye_week:
            problems.append(f"- **{p.name}** ({p.position}, {p.slot_position}) \u2014 BYE WEEK")
        elif status in INJURY_FLAGS:
            bits = [f"{status.title()}, proj {p.projected_points:.1f}"]
            if matchup_cell(p):
                bits.append(matchup_cell(p))
            if weather_cell(p, week_schedule):
                bits.append(weather_cell(p, week_schedule))
            problems.append(f"- **{p.name}** ({p.position}, {p.slot_position}) \u2014 {' \u00b7 '.join(bits)}")

    parts.append("### \u26a0\ufe0f Starters with a problem")
    parts.append("\n".join(problems) if problems else "No bye/injury flags on your starters.")
    parts.append("")

    # Bench upgrades — FLEX-aware via eligibleSlots
    upgrades = []
    for starter in starters:
        slot = starter.slot_position
        for b in bench:
            if slot in b.eligibleSlots and b.projected_points > starter.projected_points:
                b_matchup = matchup_cell(b)
                s_matchup = matchup_cell(starter)
                bench_bits = [f"proj {b.projected_points:.1f}"]
                if b_matchup:
                    bench_bits.append(b_matchup)
                starter_bits = [f"proj {starter.projected_points:.1f}"]
                if s_matchup:
                    starter_bits.append(s_matchup)
                reasoning = ai_upgrade_reasoning(b, starter)
                upgrades.append(
                    f"- **{b.name}** ({b.position}) {' \u00b7 '.join(bench_bits)} \u2192 over "
                    f"**{starter.name}** ({starter.position}, {slot}) {' \u00b7 '.join(starter_bits)}\n"
                    f"  {reasoning}"
                )

    parts.append("### \U0001F501 Possible bench upgrades")
    parts.append(
        "\n".join(upgrades)
        if upgrades
        else "No bench player currently outprojects a starter in an eligible slot."
    )
    parts.append("")

    waivers = waiver_suggestions(league, week, my_lineup)
    parts.append("### \U0001F3AF Waiver suggestions")
    parts.append(
        "\n".join(waivers)
        if waivers
        else "No free agent currently outprojects your weakest rostered player at their position."
    )
    parts.append("")

    if lean:
        want_tag = "High variance" if lean == "behind" else "Consistent"
        matches = []
        for b in bench:
            vol = volatility_cell(b, week)
            if want_tag in vol:
                matches.append(f"- **{b.name}** ({b.position}) proj {b.projected_points:.1f} \u2014 {vol}")
        if matches:
            if lean == "behind":
                context = (
                    "You're behind in the standings, so these bench options carry real "
                    "week-to-week upside \u2014 worth considering over a steadier pick if you "
                    "need to make up ground on a close start/sit call."
                )
            else:
                context = (
                    "You're ahead in the standings, so these bench options have been the "
                    "more consistent scorers lately \u2014 a safer bet if you'd rather protect "
                    "your position than chase a bigger week."
                )
            parts.append("### \U0001F3B2 Bench options matching your situation")
            parts.append(context)
            parts.append("")
            parts.append("\n".join(matches))
            parts.append("")

    value_adds = league_wide_value_adds(league, week, {p.position for p in my_lineup if p.slot_position != "IR"})
    if value_adds:
        parts.append("### \U0001F440 Widely-valued free agents (not roster-specific)")
        parts.append("\n".join(value_adds))
        parts.append("")

    parts.append("### Your starting lineup")
    parts.append(lineup_table(starters, with_notes=True, week_schedule=week_schedule, current_week=week))
    parts.append("")

    parts.append(f"### {opponent_name}'s starting lineup")
    parts.append(lineup_table(opp_starters, with_notes=False))

    # News — collected separately and shown as its own section, since
    # headlines are too long to sit cleanly in a table cell.
    news_lines = []
    for p in starters:
        results = get_player_news(p)
        if results:
            headline, url = results[0]
            if url:
                news_lines.append(f"- **{p.name}** \u2014 {headline} ([link]({url}))")
            else:
                news_lines.append(f"- **{p.name}** \u2014 {headline}")
    if news_lines:
        parts.append("")
        parts.append("### \U0001F4F0 Recent news")
        parts.append("\n".join(news_lines))

    parts.append("")
    parts.append("### \U0001F3C6 League standings")
    parts.append(league_standings_table(league, TEAM_ID))

    return "\n".join(parts)


def create_github_issue(title, body):
    resp = requests.post(
        f"https://api.github.com/repos/{GH_REPOSITORY}/issues",
        headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"title": title, "body": body},
        timeout=30,
    )
    resp.raise_for_status()


def main():
    league = League(league_id=LEAGUE_ID, year=YEAR, espn_s2=ESPN_S2, swid=SWID)
    team = get_my_team(league)
    week = league.current_week
    week_schedule = get_week_schedule(YEAR, week)
    report = build_report(league, team, week_schedule)
    print(report)  # also shows up in the GitHub Actions run log
    day_name = datetime.now(timezone.utc).strftime("%A")
    create_github_issue(f"Fantasy lineup check \u2014 Week {week} ({day_name})", report)


if __name__ == "__main__":
    main()
