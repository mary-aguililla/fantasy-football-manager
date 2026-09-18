#!/usr/bin/env python3
"""
Weekly Sleeper fantasy football lineup check.

This is a Sleeper-based fork of an ESPN version of the same script. It
runs on the same schedule (Tuesday + Thursday, via GitHub Actions) and
produces the same kind of report, but pulls its data from Sleeper's
public API instead of espn_api.

Read the "Differences from the ESPN version" section in the README
before relying on this for anything ESPN's version used to do — Sleeper
simply doesn't publish some of the data ESPN does (real weekly
projections, ownership/start percentages, defense-vs-position difficulty
ranks), so a few features are adapted or dropped rather than ported
one-for-one. Every adaptation is called out in comments at the point
it's made.

No login/cookies needed — Sleeper's league data is public and readable
with no auth, so unlike the ESPN version there's no equivalent of
ESPN_S2/SWID to manage.

The report is built as Markdown (tables + sections), since GitHub
renders that directly in the Issue body.

All config comes from environment variables (set as GitHub Actions
secrets — see the README for the full list and how to find each one).
"""

import os
import statistics
from datetime import datetime, timezone
import requests

# ---- Config ----
SLEEPER_LEAGUE_ID = os.environ["SLEEPER_LEAGUE_ID"]
SLEEPER_USERNAME = os.environ["SLEEPER_USERNAME"]
YEAR = os.environ.get("LEAGUE_YEAR", "2026")

GH_TOKEN = os.environ["GH_TOKEN"]
# GITHUB_REPOSITORY is auto-set by Actions on every run — "you/repo-name".
GH_REPOSITORY = os.environ["GITHUB_REPOSITORY"]

SLEEPER_API = "https://api.sleeper.app/v1"

BENCH_SLOTS = {"BN", "IR"}
INJURY_FLAGS = {"QUESTIONABLE", "DOUBTFUL", "OUT", "IR", "PUP", "SUSPENDED", "NA"}

# Slot eligibility for bench-upgrade comparisons. Sleeper leagues vary in
# which flex slots they run (FLEX, SUPER_FLEX, WRRB_FLEX, REC_FLEX,
# IDP slots, ...); this covers the common skill-position ones. Anything
# not listed here just falls back to "eligible for its own position
# only," which is the safe default — it means a genuinely unusual slot
# might miss a valid upgrade suggestion, but it will never suggest an
# incompatible swap.
FLEX_ELIGIBILITY = {
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}


def eligible_slots_for(position):
    """Which starting slots a player at this position could fill —
    their own position plus any flex slot that accepts it. Mirrors the
    role ESPN's `eligibleSlots` field played in the original script."""
    slots = {position}
    for flex_slot, positions in FLEX_ELIGIBILITY.items():
        if position in positions:
            slots.add(flex_slot)
    return slots


# Static stadium reference: lat/lon + dome status, keyed by standard NFL
# team abbreviations (same ones Sleeper uses in its player records).
# Hardcoded deliberately — static, rarely-changing data that can't fail
# at runtime the way an API call could. Retractable-roof stadiums (ARI,
# ATL, DAL, HOU, IND) are marked dome: True since they're closed for
# most games; the only miss is a roof left open on a nice day, which
# just means no note instead of an unneeded one.
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
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_weather_cache = {}
_week_stats_cache = {}
_week_schedule_cache = {}

# Groq config (api.groq.com) — same AI-reasoning setup as the ESPN
# version, unchanged, since it just takes facts in and returns a
# sentence; it doesn't care which fantasy platform the facts came from.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")

# Overwritten by build_report() once the league's actual scoring settings
# are known (see scoring_key()). Defaulted here so fetch_projections /
# fetch_week_actuals never hit a NameError if ever called before that.
SCORING_KEY = "pts_ppr"


# ---- Sleeper data layer ----

def sleeper_get(url, params=None, timeout=20):
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def get_my_user_id():
    data = sleeper_get(f"{SLEEPER_API}/user/{SLEEPER_USERNAME}")
    if not data or "user_id" not in data:
        raise SystemExit(
            f"Sleeper username {SLEEPER_USERNAME!r} not found. Double-check "
            f"the SLEEPER_USERNAME secret — it's the username, not a display name."
        )
    return data["user_id"]


def get_league():
    return sleeper_get(f"{SLEEPER_API}/league/{SLEEPER_LEAGUE_ID}")


def get_rosters():
    return sleeper_get(f"{SLEEPER_API}/league/{SLEEPER_LEAGUE_ID}/rosters")


def get_users():
    return sleeper_get(f"{SLEEPER_API}/league/{SLEEPER_LEAGUE_ID}/users")


def get_matchups(week):
    return sleeper_get(f"{SLEEPER_API}/league/{SLEEPER_LEAGUE_ID}/matchups/{week}")


def get_current_week():
    """Sleeper's own idea of 'what week is it', same role league.current_week
    played in the ESPN version. Falls back to week 1 if the state endpoint
    is ever unreachable, rather than crashing — a wrong week 1 fallback is
    at least an obviously-wrong report, not a dead workflow run."""
    try:
        state = sleeper_get(f"{SLEEPER_API}/state/nfl")
        return int(state.get("week") or 1)
    except Exception:
        return 1


def scoring_key(league_settings):
    """Which of Sleeper's pts_ppr / pts_half_ppr / pts_std fields matches
    this league's actual scoring, based on the reception point value —
    avoids assuming every league is PPR."""
    rec = ((league_settings or {}).get("scoring_settings") or {}).get("rec", 0)
    if rec >= 1:
        return "pts_ppr"
    if rec >= 0.5:
        return "pts_half_ppr"
    return "pts_std"


def load_players_master():
    """Sleeper's full player dictionary (~5MB, every player_id -> name/
    position/team/injury status). Sleeper's own docs ask that this not be
    called more than once a day — fine here since this script only runs
    twice a week, each run a fresh fetch."""
    return sleeper_get(f"{SLEEPER_API}/players/nfl")


def fetch_projections(week):
    """Best-effort pull of real per-player projections for this week.

    This hits an *undocumented* Sleeper endpoint (not part of their
    published public API) that the sleeper.com web app itself uses. It
    could change shape or disappear without notice. That's exactly why
    every call site treats a missing projection as "fall back to season
    average" rather than "crash" — see build_player_view(). Confirm this
    still works for you the same way the README has you test-run the
    ESPN version before trusting it.
    """
    if week in _week_stats_cache.get("_projections", {}):
        return _week_stats_cache["_projections"][week]
    try:
        data = sleeper_get(
            f"https://api.sleeper.app/projections/nfl/{YEAR}/{week}",
            params={"season_type": "regular"},
        )
        key = SCORING_KEY
        result = {}
        for item in data:
            pid = item.get("player_id")
            stats = item.get("stats") or {}
            pts = stats.get(key)
            if pid and pts is not None:
                result[pid] = float(pts)
    except Exception:
        result = {}
    _week_stats_cache.setdefault("_projections", {})[week] = result
    return result


def fetch_week_actuals(week):
    """Actual fantasy points scored by every player league-wide in a
    completed week (same undocumented endpoint family as
    fetch_projections, just the results side instead of the outlook
    side). Used to build season averages and recent-form trends without
    needing per-roster history for players who aren't on your roster."""
    if week in _week_stats_cache:
        return _week_stats_cache[week]
    try:
        data = sleeper_get(
            f"https://api.sleeper.app/stats/nfl/{YEAR}/{week}",
            params={"season_type": "regular"},
        )
        key = SCORING_KEY
        result = {}
        for item in data:
            pid = item.get("player_id")
            stats = item.get("stats") or {}
            pts = stats.get(key)
            if pid and pts is not None:
                result[pid] = float(pts)
    except Exception:
        result = {}
    _week_stats_cache[week] = result
    return result


def fetch_trending_adds(limit=100):
    """player_id -> add count over the last 24h, league-wide across all
    of Sleeper. Used as a rough substitute for ESPN's ownership/start
    percentages, which Sleeper's public API doesn't expose at all —
    "a lot of people are picking this player up right now" instead of
    "a lot of people already own this player." Different signal, same
    rough intent: explain why a free agent is worth a second look."""
    try:
        data = sleeper_get(
            f"{SLEEPER_API}/players/nfl/trending/add",
            params={"lookback_hours": 24, "limit": limit},
        )
        return {item["player_id"]: item["count"] for item in data if "player_id" in item}
    except Exception:
        return {}


def get_week_schedule(week):
    """One call for a week's NFL slate: opponent, home/away, and kickoff
    date per team. Uses ESPN's own public (non-fantasy) scoreboard API —
    this is general NFL schedule data, not tied to an ESPN fantasy
    account, so it's fair game to keep for a Sleeper-based script too.
    A team on bye that week is simply absent from the result.
    Best-effort — returns {} on any hiccup, which just means
    weather/bye notes go blank for that week, not a crash."""
    if week in _week_schedule_cache:
        return _week_schedule_cache[week]
    try:
        resp = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
            params={"dates": YEAR, "seasontype": 2, "week": week},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        result = {}
        for event in data.get("events", []):
            event_date = event.get("date")
            for competition in event.get("competitions", []):
                competitors = competition.get("competitors", [])
                if len(competitors) != 2:
                    continue
                for i, comp in enumerate(competitors):
                    abbr = comp.get("team", {}).get("abbreviation")
                    home_away = comp.get("homeAway")
                    opp_abbr = competitors[1 - i].get("team", {}).get("abbreviation")
                    if abbr and opp_abbr:
                        result[abbr] = {
                            "opponent": opp_abbr,
                            "home": home_away == "home",
                            "date": event_date,
                        }
        result = result
    except Exception:
        result = {}
    _week_schedule_cache[week] = result
    return result


# ---- Player view: a small ESPN-Player-shaped wrapper ----
#
# Everything below this point (matchup/weather/trend/volatility/AI
# reasoning) was written against espn_api's Player object in the ESPN
# version. Rather than rewrite each of those, this wraps Sleeper's raw
# data in an object exposing the same attribute names, so that logic
# ports over close to verbatim. Fields Sleeper has no equivalent for
# (percent_owned, percent_started, defense-vs-position rank) are left at
# safe "not available" sentinel values, which the original code already
# treats as "don't show this," rather than being newly special-cased.

class PlayerView:
    def __init__(self, player_id, meta, slot_position):
        self.player_id = player_id
        self.slot_position = slot_position
        self.name = (
            meta.get("full_name")
            or f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()
            or player_id
        )
        self.position = meta.get("position") or (meta.get("fantasy_positions") or [None])[0] or "?"
        self.proTeam = meta.get("team") or "None"
        self.eligibleSlots = eligible_slots_for(self.position)
        self.injuryStatus = (meta.get("injury_status") or "").upper()
        self.active_status = "inactive" if meta.get("status") in ("Inactive", "Retired") else ""
        # Sleeper has no public ownership/start-rate data — left at the
        # same "not available" sentinel the ESPN version's own optional
        # fields already default to via getattr(..., -1).
        self.percent_owned = -1
        self.percent_started = -1
        self.trending_adds = 0
        # Filled in by attach_schedule_info() once a week_schedule is on hand.
        self.pro_opponent = None
        self.pro_home = False
        self.on_bye_week = False
        self.game_date = None
        # Filled in by the caller once projections/history are on hand.
        self.projected_points = 0.0
        self.avg_points = 0.0
        self.used_avg_fallback = False
        self.stats = {}


def attach_schedule_info(player, week_schedule):
    entry = week_schedule.get(player.proTeam)
    if entry is None:
        # Team not on this week's slate at all — bye week. (Defenses and
        # players with no NFL team, e.g. free agents/retired, also land
        # here; on_bye_week only matters for players actually in a
        # starting lineup, so a harmless false positive for those is fine.)
        player.on_bye_week = player.proTeam not in ("None", None)
        return
    player.pro_opponent = entry["opponent"]
    player.pro_home = entry["home"]
    if entry.get("date"):
        try:
            player.game_date = datetime.fromisoformat(entry["date"].replace("Z", "+00:00"))
        except Exception:
            player.game_date = None


def build_player_view(player_id, meta, slot_position, projections, week_actuals_by_week, current_week):
    player = PlayerView(player_id, meta, slot_position)

    proj = projections.get(player_id)
    history = {
        w: {"points": week_actuals_by_week[w][player_id]}
        for w in range(max(1, current_week - 8), current_week)
        if player_id in week_actuals_by_week.get(w, {})
    }
    player.stats = history
    if history:
        player.avg_points = sum(v["points"] for v in history.values()) / len(history)

    if proj is not None:
        player.projected_points = proj
    else:
        # No live projection available (endpoint down, or a player with
        # no projection posted, e.g. a deep bench/practice-squad name) —
        # fall back to season average. used_avg_fallback drives the "~"
        # marker in the report so this is visible, not silently blended
        # in as if it were an equally reliable number.
        player.projected_points = player.avg_points
        player.used_avg_fallback = True

    return player


def proj_str(player):
    marker = "~" if player.used_avg_fallback else ""
    return f"{player.projected_points:.1f}{marker}"


# ---- Reasoning (unchanged from the ESPN version) ----

def upgrade_reasoning(bench_proj, starter_proj, bench_matchup, starter_matchup):
    """1-2 template sentences explaining a bench-upgrade suggestion —
    how big the projection gap actually is. Rule-based logic over
    numbers already computed here, not AI reasoning. (The ESPN
    version's matchup-agreement sentence is dropped here since this
    matchup_cell no longer carries a difficulty judgment — see
    matchup_cell's docstring below.)"""
    gap = bench_proj - starter_proj
    if gap >= 3:
        return f"That's a clear enough gap ({gap:.1f} pts) to be a fairly confident swap."
    return f"The gap is thin ({gap:.1f} pts), so this one's closer than it looks."


def call_ai_model(prompt):
    """Calls Groq's chat completions endpoint (OpenAI-compatible), as a
    plain chat request — no tool use. Needs its own GROQ_API_KEY secret
    (free, no credit card at signup) — separate account from GitHub, not
    reusing GH_TOKEN. Returns the response text, or None on any failure —
    this must never be what breaks a run."""
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
                "max_completion_tokens": 800,
                "stream": False,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"AI reasoning call failed ({resp.status_code}): {resp.text[:500]}")
            return None
        choice = resp.json()["choices"][0]
        content = choice["message"]["content"].strip()
        if not content:
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
    """AI-generated reasoning for a bench-upgrade suggestion. No player
    news here (see get_player_news's docstring for why), so this leans
    on projections and matchup context alone. Falls back to the
    rule-based upgrade_reasoning() if the AI call fails for any reason."""
    bench_matchup = matchup_cell(bench_player)
    starter_matchup = matchup_cell(starter)

    facts = [
        f"Bench option: {bench_player.name} ({bench_player.position}), "
        f"projected {proj_str(bench_player)} points"
        f"{' (season average used as a stand-in — a live projection was not available)' if bench_player.used_avg_fallback else ''}. "
        f"Matchup: {bench_matchup or 'no notable matchup data'}.",
        f"Current starter: {starter.name} ({starter.position}, {starter.slot_position}), "
        f"projected {proj_str(starter)} points"
        f"{' (season average used as a stand-in — a live projection was not available)' if starter.used_avg_fallback else ''}. "
        f"Matchup: {starter_matchup or 'no notable matchup data'}.",
    ]
    prompt = (
        "You're helping someone decide on a fantasy football lineup swap. "
        "Using ONLY the facts below — don't invent or assume anything not "
        "stated here — write 1-2 concise, plain-spoken sentences on whether "
        "this swap makes sense. If a projection is flagged as a season-average "
        "stand-in, treat it as less certain than a real weekly projection would be, "
        "and say so if it changes your confidence.\n\n" + "\n".join(facts)
    )
    result = call_ai_model(prompt)
    if result:
        return result
    return upgrade_reasoning(
        bench_player.projected_points, starter.projected_points, bench_matchup, starter_matchup
    )


def ai_availability_reasoning(player):
    """AI-generated one-sentence take on why a free agent might be worth
    (or not worth) a look, despite the projection shown.

    Adapted from the ESPN version: that one leaned on ownership/start
    percentages, which Sleeper's public API doesn't expose. This uses
    Sleeper's trending-adds count instead — a momentum signal ("people
    are picking this player up right now") rather than a snapshot
    ownership stat. No player-news headlines either, for the same
    reason build_report drops the news section — see get_player_news.
    Returns '' (not a fallback sentence) if the AI call fails."""
    note = availability_note(player)
    facts = [
        f"Player: {player.name} ({player.position}). "
        f"Projected {proj_str(player)} points"
        f"{' (season average, not a live weekly projection)' if player.used_avg_fallback else ''}.",
        f"Availability flag: {note or 'none — appears active and healthy'}.",
        f"Added by {player.trending_adds} Sleeper managers league-wide in the last 24 hours"
        if player.trending_adds else "Not currently trending as a widely-added player.",
    ]
    prompt = (
        "You're helping a fantasy football manager decide whether a free "
        "agent is worth adding. Using ONLY the facts below, write ONE "
        "concise sentence on what they should take from this. If nothing "
        "here actually supports a strong opinion either way, say so "
        "plainly rather than invent a reason.\n\n" + "\n".join(facts)
    )
    return call_ai_model(prompt) or ""


def availability_note(player):
    """Short flag explaining why a free agent might be available despite
    a decent projection — an injury designation, a bye week, or
    otherwise inactive. '' when there's no red flag."""
    if player.injuryStatus in INJURY_FLAGS:
        return player.injuryStatus.title()
    if player.on_bye_week:
        return "Bye week"
    if player.active_status == "inactive":
        return "Inactive"
    return ""


def matchup_cell(player):
    """'@DEN' / 'vs DEN' / '' — plain opponent + home/away info.

    Unlike the ESPN version's matchup_cell, this carries no difficulty
    judgment ("Tough vs.../Favorable vs...") — that was based on ESPN's
    defense-vs-position rank data, which Sleeper's public API has no
    equivalent for. This is informational only."""
    if not player.pro_opponent:
        return ""
    return f"vs {player.pro_opponent}" if player.pro_home else f"@{player.pro_opponent}"


def weather_cell(player):
    """'⚠️ 74% rain, Denver, CO' / '' — outdoor games only, only when
    wind or rain chance is a real outlier. Unchanged in spirit from the
    ESPN version; only the source of the game date changed (from
    espn_api's player.game_date to the ESPN public-schedule lookup this
    script already does for bye/opponent info)."""
    team = player.proTeam
    stadium = TEAM_STADIUMS.get(team)
    if not stadium or stadium["dome"]:
        return ""
    if not player.pro_opponent:
        return ""

    venue_abbr = team if player.pro_home else player.pro_opponent
    venue = TEAM_STADIUMS.get(venue_abbr)
    if not venue or venue["dome"]:
        return ""
    if not player.game_date:
        return ""

    forecast = fetch_weather(venue["lat"], venue["lon"], player.game_date.date())
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
    return f"⚠️ {', '.join(flags)}, {venue['city']}"


def fetch_weather(lat, lon, date):
    """Daily max wind + precip probability via Open-Meteo (free, no
    key). Cached per (location, date) so players in the same game only
    trigger one call. Unchanged from the ESPN version."""
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


def trend_cell(player, lookback=3):
    """'\U0001F4C8 18.2 last 2wk vs 9.4 szn' / '\U0001F4C9 ...' / '' —
    only flags a real outlier (>=20% deviation from season average), and
    only once there's at least one completed week of history. Unchanged
    in spirit from the ESPN version; player.stats here comes from
    Sleeper's stats endpoint instead of espn_api."""
    weeks = sorted(player.stats.keys())[-lookback:]
    if not weeks or not player.avg_points:
        return ""
    recent_avg = sum(player.stats[w]["points"] for w in weeks) / len(weeks)
    diff = (recent_avg - player.avg_points) / player.avg_points
    if diff >= 0.20:
        return f"\U0001F4C8 {recent_avg:.1f} last {len(weeks)}wk vs {player.avg_points:.1f} szn"
    if diff <= -0.20:
        return f"\U0001F4C9 {recent_avg:.1f} last {len(weeks)}wk vs {player.avg_points:.1f} szn"
    return ""


def volatility_cell(player, lookback=5):
    """'\U0001F3B2 High variance' / '\U0001F6E1️ Consistent' / '' —
    coefficient of variation over up to the last 5 completed weeks.
    Needs at least 3 data points to be meaningful. Unchanged in spirit
    from the ESPN version."""
    weeks = sorted(player.stats.keys())[-lookback:]
    if len(weeks) < 3:
        return ""
    pts = [player.stats[w]["points"] for w in weeks]
    mean = sum(pts) / len(pts)
    if mean <= 0:
        return ""
    cv = statistics.pstdev(pts) / mean
    if cv >= 0.6:
        return "\U0001F3B2 High variance"
    if cv <= 0.25:
        return "\U0001F6E1️ Consistent"
    return ""


def strategy_lean(wins, losses):
    """Returns ('ahead'|'behind'|None, note_text) — a simple win/loss
    threshold, not AI reasoning. Unchanged from the ESPN version."""
    if wins > losses:
        return "ahead", "You're ahead in the standings — favor the safer, more consistent option on close start/sit calls."
    if losses > wins:
        return "behind", "You're behind in the standings — a week to lean toward upside over floor on close calls."
    return None, ""


def playoff_picture_note(league, my_roster, standings):
    """One line on playoff standing, using league.settings.playoff_week_start
    and the regular-season length implied by it."""
    try:
        playoff_start = league["settings"]["playoff_week_start"]
        # Sleeper doesn't publish "how many teams make the playoffs" as a
        # single settings field the way ESPN does — leagues configure it
        # via bracket size, which isn't in this endpoint. Approximating
        # with a common default (half the league, rounded down) rather
        # than guessing a specific number with false confidence.
        playoff_spots = max(2, len(standings) // 2)
    except Exception:
        return ""
    current_week = get_current_week()
    weeks_left = max(0, playoff_start - current_week)
    my_rank = next((i + 1 for i, r in enumerate(standings) if r["roster_id"] == my_roster["roster_id"]), None)
    if my_rank is None:
        return ""
    if my_rank <= playoff_spots:
        return f"In an approximate playoff spot (#{my_rank} of ~{playoff_spots}), {weeks_left} weeks left in the regular season. (Sleeper doesn't expose your league's exact playoff-team count — this is an estimate at half the league.)"
    return f"Outside an approximate playoff cutoff by {my_rank - playoff_spots} spot(s) (#{my_rank}, ~{playoff_spots} spots estimated), {weeks_left} weeks left."


def league_wide_value_adds(rostered_ids, players_master, projections, week_actuals_by_week, current_week, roster_positions, trending, limit=5):
    """Free agents getting picked up across Sleeper right now, filtered
    to positions you actually roster. Adapted from the ESPN version's
    ownership-percentage-based version — see ai_availability_reasoning's
    docstring for why trending-adds replaces percent_owned here."""
    lines = []
    for pos in sorted(roster_positions):
        candidates = get_free_agents(rostered_ids, players_master, pos, projections, week_actuals_by_week, current_week, limit=30)
        notable = [c for c in candidates if trending.get(c.player_id, 0) > 0 and c.projected_points > 0]
        notable.sort(key=lambda c: trending.get(c.player_id, 0), reverse=True)
        for c in notable[:2]:
            bits = [f"{trending[c.player_id]} adds/24h", f"proj {proj_str(c)}"]
            note = availability_note(c)
            if note:
                bits.append(f"⚠️ {note}")
            line = f"- **{c.name}** ({pos}) — {' · '.join(bits)}"
            context = ai_availability_reasoning(c)
            if context:
                line += f"\n  {context}"
            lines.append(line)
    return lines[:limit]


def get_free_agents(rostered_ids, players_master, position, projections, week_actuals_by_week, current_week, limit=15):
    """Rank-and-filter Sleeper's full player dictionary down to
    plausible, projected, not-already-rostered players at a position.
    Sleeper's public API has no dedicated free-agent endpoint like
    ESPN's `league.free_agents()` — this does the same job by
    subtracting every rostered player_id from the full player pool."""
    candidates = []
    for pid, meta in players_master.items():
        if pid in rostered_ids:
            continue
        fantasy_positions = meta.get("fantasy_positions") or []
        if position != meta.get("position") and position not in fantasy_positions:
            continue
        if meta.get("status") in ("Retired", "Inactive", "None") and not meta.get("team"):
            continue
        player = build_player_view(pid, meta, "FA", projections, week_actuals_by_week, current_week)
        if player.projected_points > 0:
            candidates.append(player)
    candidates.sort(key=lambda p: p.projected_points, reverse=True)
    return candidates[:limit]


def waiver_suggestions(roster_players, rostered_ids, players_master, projections, week_actuals_by_week, current_week):
    """For each position on your roster (excluding IR), compares your
    weakest rostered player at that position against available free
    agents. Surfaces the clearest upgrade per position, if any exists.
    Unchanged in spirit from the ESPN version."""
    by_position = {}
    for p in roster_players:
        if p.slot_position == "IR":
            continue
        by_position.setdefault(p.position, []).append(p)

    lines = []
    for pos in sorted(by_position):
        worst = min(by_position[pos], key=lambda p: p.projected_points)
        candidates = get_free_agents(rostered_ids, players_master, pos, projections, week_actuals_by_week, current_week, limit=5)
        for c in candidates:
            if c.projected_points > worst.projected_points:
                bits = [f"proj {proj_str(c)}"]
                note = availability_note(c)
                if note:
                    bits.append(f"⚠️ {note}")
                line = (
                    f"- **{c.name}** ({pos}) — {' · '.join(bits)} "
                    f"→ outprojects your **{worst.name}** (proj {proj_str(worst)})"
                )
                context = ai_availability_reasoning(c)
                if context:
                    line += f"\n  {context}"
                lines.append(line)
                break
    return lines


def league_standings_table(standings, my_roster_id, team_names):
    """Markdown table of the full league, ordered by wins/points.
    Sleeper doesn't compute a single 'standing' field the way ESPN
    does — this sorts by wins then points-for, the common tiebreak."""
    lines = ["| # | Team | Record | PF | PA |", "|---|---|---|---|---|"]
    for i, r in enumerate(standings, start=1):
        s = r.get("settings", {})
        wins, losses, ties = s.get("wins", 0), s.get("losses", 0), s.get("ties", 0)
        record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")
        pf = s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100
        pa = s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100
        name = team_names.get(r["roster_id"], f"Roster {r['roster_id']}")
        if r["roster_id"] == my_roster_id:
            name = f"**{name}**"
        lines.append(f"| {i} | {name} | {record} | {pf:.1f} | {pa:.1f} |")
    return "\n".join(lines)


def lineup_table(players, with_notes):
    """Markdown table for a lineup. with_notes=True adds Matchup/Weather/
    Trend columns (used for your lineup only, not the opponent's).
    The Proj column shows a '~' suffix wherever a season-average
    fallback was used instead of a real projection — see PlayerView."""
    if with_notes:
        lines = ["| Slot | Player | Pos | Proj | Matchup | Weather | Trend |",
                 "|---|---|---|---|---|---|---|"]
    else:
        lines = ["| Slot | Player | Pos | Proj |", "|---|---|---|---|"]

    slot_order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
    for p in sorted(players, key=lambda x: slot_order.get(x.slot_position, 9)):
        row = f"| {p.slot_position} | {p.name} | {p.position} | {proj_str(p)}"
        if with_notes:
            row += f" | {matchup_cell(p)} | {weather_cell(p)} | {trend_cell(p)}"
        row += " |"
        lines.append(row)
    return "\n".join(lines)


def _last_name(full_name):
    parts = full_name.replace(".", "").split()
    while len(parts) > 1 and parts[-1].lower() in NAME_SUFFIXES:
        parts.pop()
    return parts[-1] if parts else full_name


def get_player_news(player):
    """No-op: unlike the ESPN version, this always returns [].

    ESPN's player-news API (used in the original script) is keyed to
    ESPN's own fantasy player IDs, which don't correspond to Sleeper's
    player IDs — there's no reliable public mapping between the two
    without an extra, fragile name-matching step. Rather than ship a
    news feature that quietly breaks or mismatches players, this drops
    it. Kept as a function (returning an empty list, the same
    "no news found" shape the rest of the code already expects) so
    build_report and ai_upgrade_reasoning don't need special-casing —
    if you want this back, this is the one place to wire up a
    Sleeper-ID-aware news source."""
    return []


# ---- Report assembly ----

def build_report():
    league = get_league()
    global SCORING_KEY
    SCORING_KEY = scoring_key(league)

    my_user_id = get_my_user_id()
    rosters = get_rosters()
    users = get_users()
    team_names = {
        u["user_id"]: (u.get("metadata") or {}).get("team_name") or u.get("display_name") or "Unnamed team"
        for u in users
    }
    roster_team_names = {r["roster_id"]: team_names.get(r["owner_id"], "Unnamed team") for r in rosters}

    my_roster = next((r for r in rosters if r.get("owner_id") == my_user_id), None)
    if my_roster is None:
        raise SystemExit(
            f"No roster in league {SLEEPER_LEAGUE_ID} is owned by "
            f"{SLEEPER_USERNAME!r}. Double-check SLEEPER_LEAGUE_ID and "
            f"SLEEPER_USERNAME."
        )

    week = get_current_week()
    week_schedule = get_week_schedule(week)
    projections = fetch_projections(week)
    players_master = load_players_master()
    trending = fetch_trending_adds()

    week_actuals_by_week = {w: fetch_week_actuals(w) for w in range(max(1, week - 8), week)}

    rostered_ids = set()
    for r in rosters:
        rostered_ids.update(r.get("players") or [])

    roster_positions = [p for p in league.get("roster_positions", []) if p not in ("BN", "IR")]

    def make_lineup(roster):
        starters = roster.get("starters") or []
        all_ids = roster.get("players") or []
        reserve = set(roster.get("reserve") or [])
        players = []
        for idx, pid in enumerate(starters):
            if pid in (None, "0"):
                continue
            slot = roster_positions[idx] if idx < len(roster_positions) else "FLEX"
            meta = players_master.get(pid, {})
            p = build_player_view(pid, meta, slot, projections, week_actuals_by_week, week)
            attach_schedule_info(p, week_schedule)
            players.append(p)
        for pid in all_ids:
            if pid in starters or pid in (None, "0"):
                continue
            slot = "IR" if pid in reserve else "BN"
            meta = players_master.get(pid, {})
            p = build_player_view(pid, meta, slot, projections, week_actuals_by_week, week)
            attach_schedule_info(p, week_schedule)
            players.append(p)
        return players

    my_lineup = make_lineup(my_roster)
    starters = [p for p in my_lineup if p.slot_position not in BENCH_SLOTS]
    bench = [p for p in my_lineup if p.slot_position in BENCH_SLOTS]

    # Find this week's opponent via the matchups endpoint.
    matchups = get_matchups(week)
    my_entry = next((m for m in matchups if m.get("roster_id") == my_roster["roster_id"]), None)
    opp_lineup, opp_starters, opponent_name = [], [], "a bye"
    my_projected = sum(p.projected_points for p in starters)
    opp_projected = 0.0
    if my_entry and my_entry.get("matchup_id") is not None:
        opp_entry = next(
            (m for m in matchups
             if m.get("matchup_id") == my_entry["matchup_id"] and m.get("roster_id") != my_roster["roster_id"]),
            None,
        )
        if opp_entry:
            opp_roster = next((r for r in rosters if r["roster_id"] == opp_entry["roster_id"]), None)
            if opp_roster:
                opponent_name = roster_team_names.get(opp_roster["roster_id"], "Opponent")
                opp_lineup = make_lineup(opp_roster)
                opp_starters = [p for p in opp_lineup if p.slot_position not in BENCH_SLOTS]
                opp_projected = sum(p.projected_points for p in opp_starters)

    settings = my_roster.get("settings", {})
    wins, losses, ties = settings.get("wins", 0), settings.get("losses", 0), settings.get("ties", 0)
    record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")

    standings = sorted(
        rosters,
        key=lambda r: (-(r.get("settings", {}).get("wins", 0)), -(r.get("settings", {}).get("fpts", 0))),
    )
    my_rank = next((i + 1 for i, r in enumerate(standings) if r["roster_id"] == my_roster["roster_id"]), "?")
    my_team_name = roster_team_names.get(my_roster["roster_id"], SLEEPER_USERNAME)

    parts = [
        f"## Week {week} — {my_team_name} vs {opponent_name}",
        f"**Record:** {record} (#{my_rank} of {len(rosters)})  "
        f"**Projected:** {my_projected:.1f} vs {opp_projected:.1f}",
        "*Proj values marked '~' are a season-average stand-in used because a live "
        "weekly projection wasn't available — see the README for why.*",
    ]
    lean, strategy_text = strategy_lean(wins, losses)
    if strategy_text:
        parts.append(f"*{strategy_text}*")
    playoff_line = playoff_picture_note(league, my_roster, standings)
    if playoff_line:
        parts.append(f"*{playoff_line}*")
    parts.append("")

    # Bye / injury flags
    problems = []
    for p in starters:
        if p.on_bye_week:
            problems.append(f"- **{p.name}** ({p.position}, {p.slot_position}) — BYE WEEK")
        elif p.injuryStatus in INJURY_FLAGS:
            bits = [f"{p.injuryStatus.title()}, proj {proj_str(p)}"]
            if matchup_cell(p):
                bits.append(matchup_cell(p))
            if weather_cell(p):
                bits.append(weather_cell(p))
            problems.append(f"- **{p.name}** ({p.position}, {p.slot_position}) — {' · '.join(bits)}")

    parts.append("### ⚠️ Starters with a problem")
    parts.append("\n".join(problems) if problems else "No bye/injury flags on your starters.")
    parts.append("")

    # Bench upgrades — flex-aware via eligibleSlots
    upgrades = []
    for starter in starters:
        slot = starter.slot_position
        for b in bench:
            if slot in b.eligibleSlots and b.projected_points > starter.projected_points:
                bench_bits = [f"proj {proj_str(b)}"]
                if matchup_cell(b):
                    bench_bits.append(matchup_cell(b))
                starter_bits = [f"proj {proj_str(starter)}"]
                if matchup_cell(starter):
                    starter_bits.append(matchup_cell(starter))
                reasoning = ai_upgrade_reasoning(b, starter)
                upgrades.append(
                    f"- **{b.name}** ({b.position}) {' · '.join(bench_bits)} → over "
                    f"**{starter.name}** ({starter.position}, {slot}) {' · '.join(starter_bits)}\n"
                    f"  {reasoning}"
                )

    parts.append("### \U0001F501 Possible bench upgrades")
    parts.append(
        "\n".join(upgrades)
        if upgrades
        else "No bench player currently outprojects a starter in an eligible slot."
    )
    parts.append("")

    waivers = waiver_suggestions(my_lineup, rostered_ids, players_master, projections, week_actuals_by_week, week)
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
            vol = volatility_cell(b)
            if want_tag in vol:
                matches.append(f"- **{b.name}** ({b.position}) proj {proj_str(b)} — {vol}")
        if matches:
            context = (
                "You're behind in the standings, so these bench options carry real "
                "week-to-week upside — worth considering over a steadier pick if you "
                "need to make up ground on a close start/sit call."
                if lean == "behind" else
                "You're ahead in the standings, so these bench options have been the "
                "more consistent scorers lately — a safer bet if you'd rather protect "
                "your position than chase a bigger week."
            )
            parts.append("### \U0001F3B2 Bench options matching your situation")
            parts.append(context)
            parts.append("")
            parts.append("\n".join(matches))
            parts.append("")

    value_adds = league_wide_value_adds(
        rostered_ids, players_master, projections, week_actuals_by_week, week,
        {p.position for p in my_lineup if p.slot_position != "IR"}, trending,
    )
    if value_adds:
        parts.append("### \U0001F440 Trending free agents (not roster-specific)")
        parts.append("\n".join(value_adds))
        parts.append("")

    parts.append("### Your starting lineup")
    parts.append(lineup_table(starters, with_notes=True))
    parts.append("")

    parts.append(f"### {opponent_name}'s starting lineup")
    parts.append(lineup_table(opp_starters, with_notes=False) if opp_starters else "No opponent this week (bye).")

    parts.append("")
    parts.append("### \U0001F3C6 League standings")
    parts.append(league_standings_table(standings, my_roster["roster_id"], roster_team_names))

    return "\n".join(parts), week


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
    report, week = build_report()
    print(report)  # also shows up in the GitHub Actions run log
    day_name = datetime.now(timezone.utc).strftime("%A")
    create_github_issue(f"Fantasy lineup check — Week {week} ({day_name})", report)


if __name__ == "__main__":
    main()
