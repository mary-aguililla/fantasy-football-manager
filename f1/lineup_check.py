#!/usr/bin/env python3
"""
F1 Fantasy race-weekend team check.

This is an F1-Fantasy-flavored sibling of the ESPN/Sleeper lineup
checkers in this repo. It doesn't run on a fixed weekday like the NFL
versions, because F1 doesn't have one: a race weekend's fantasy deadline
is qualifying, and the calendar has uneven gaps (including back-to-back
weekends). Instead this script is meant to run on a frequent schedule
(daily is the suggestion in the README) and no-ops quietly unless the
next qualifying session is coming up within DEADLINE_WINDOW_DAYS — see
main(). That keeps a daily cron safe to leave on without spamming an
Issue every day of the season.

Data comes from three independent sources, each used for what it's
actually good at:
  - fantasy-api.formula1.com — F1 Fantasy's own (undocumented) API.
    Used for your picked team and the full player/price pool. Auth is a
    single session cookie (F1_FANTASY_007) you copy from your browser
    once you're logged into fantasy.formula1.com's team-picker app — see
    the README. This deliberately never calls F1's real username/password
    login endpoint, which is known to trip Akamai's bot detection
    (CAPTCHA) when automated; reusing an already-established browser
    session cookie sidesteps that entirely, the same way the ESPN version
    reuses espn_s2/SWID instead of scripting a login.
  - api.jolpi.ca (Jolpica-F1, the actively-maintained successor to the
    now-retired Ergast API) — free, public, no auth, no key. Used for
    the race calendar, qualifying deadline, circuit location, and recent
    race/qualifying results. Same Ergast-compatible schema Ergast always
    had, just a different host.
  - Open-Meteo — same free weather API the ESPN/Sleeper versions use,
    just pointed at one circuit instead of 32 NFL stadiums.

Some fields on the authenticated F1 Fantasy team endpoint (budget/bank
balance, transfers remaining, which driver has DRS Boost active) aren't
documented anywhere and aren't confirmed against a real account by this
script's author — see extract_team_meta()'s docstring. Best-effort
key-sniffing is used for those, and the raw keys seen are always printed
to the run log so a quick look at one real run is enough to tighten the
parsing if the guesses are off.
"""

import os
import re
from datetime import datetime, timedelta, timezone

import requests

# ---- Config ----
F1_SESSION_COOKIE = os.environ["F1_SESSION_COOKIE"]
F1_LEAGUE_ID = os.environ["F1_LEAGUE_ID"]
LEAGUE_YEAR = os.environ.get("LEAGUE_YEAR", "2026")
# The path segment fantasy-api.formula1.com uses is an API version, not
# the season — the reference implementation this was built from used
# "2022" against live 2024+ seasons with no apparent issue. Overridable
# in case F1 ever bumps it.
FANTASY_API_VERSION = os.environ.get("F1_FANTASY_API_VERSION", "2022")
TEAM_SLOT = int(os.environ.get("F1_TEAM_SLOT", "1"))
DEADLINE_WINDOW_DAYS = int(os.environ.get("F1_DEADLINE_WINDOW_DAYS", "3"))

GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPOSITORY = os.environ["GITHUB_REPOSITORY"]

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")

FANTASY_API_BASE = f"https://fantasy-api.formula1.com/f1/{FANTASY_API_VERSION}"
# Confirmed against a real logged-in browser session (not from any
# library source): F1 sets an F1_FANTASY_007 cookie once you're actually
# inside the Fantasy team-picker app — a compact JWT whose payload is
# just {"007": <your SubscriberId>, exp, iat, nbf}, distinct from the
# heavier general-account login-session cookie F1's main site sets on
# login. This is the one fantasy-api.formula1.com's authenticated
# endpoints expect.
FANTASY_SESSION_COOKIE_NAME = "F1_FANTASY_007"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
# Jolpica asks that callers identify themselves with a real User-Agent
# rather than a generic/default one, to help them keep the free API up.
JOLPICA_HEADERS = {
    "User-Agent": "fantasy-football-manager-f1 (github.com/mary-aguililla/fantasy-football-manager)"
}

WEATHER_WIND_FLAG_MPH = 20
WEATHER_PRECIP_FLAG_PCT = 50

_weather_cache = {}


# ---- F1 Fantasy API (fantasy-api.formula1.com) ----

def fantasy_session():
    """A requests.Session carrying the one cookie (F1_FANTASY_007) F1
    Fantasy's authenticated endpoints need. This is copied by hand from a
    logged-in browser (see README) rather than obtained by scripting F1's
    own login flow — that flow is Akamai-bot-protected and known to
    CAPTCHA programmatic attempts inconsistently, even when replaying a
    valid cookie. A session cookie lifted from a real browser session
    sidesteps that step entirely, the same way the ESPN version never
    scripts an ESPN login and just reuses espn_s2/SWID."""
    s = requests.Session()
    s.cookies.set(FANTASY_SESSION_COOKIE_NAME, F1_SESSION_COOKIE, domain="formula1.com")
    return s


def get_current_user(session):
    """Your F1 Fantasy user_global_id, needed to look up your picked
    team. Derived from the session cookie alone — no separate secret to
    manage for it."""
    resp = session.get(f"{FANTASY_API_BASE}/users", params={"current": "true"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    user_id = data.get("user_global_id") or data.get("UserGlobalId") or (data.get("user") or {}).get("user_global_id")
    if not user_id:
        raise SystemExit(
            "Could not find user_global_id in the /users?current=true response — "
            f"the F1_SESSION_COOKIE secret is likely expired or wrong. Raw response: {data}"
        )
    return user_id


def get_player_pool(session):
    """Full pool of drivers and constructors: player_id -> info dict
    (name, team/position, price, season points). This is the same ID
    space picked_teams uses, which is why prices come from here rather
    than F1's separate public feeds/drivers/{round}_en.json feed — that
    feed is confirmed to need no auth, but there's no confirmation its
    IDs line up with fantasy-api.formula1.com's player_id, and a silent
    ID mismatch would be worse than one extra authenticated call.
    Documented as an unauthenticated endpoint, but sent with the session
    anyway — harmless either way."""
    resp = session.get(f"{FANTASY_API_BASE}/players", timeout=20)
    resp.raise_for_status()
    data = resp.json()
    players = data.get("players") if isinstance(data, dict) else data
    pool = {}
    for p in players or []:
        pid = p.get("id") or p.get("player_id")
        if pid is not None:
            pool[pid] = p
    return pool


def get_picked_team(session, user_global_id, slot):
    resp = session.get(
        f"{FANTASY_API_BASE}/picked_teams/for_slot",
        params={"slot": slot, "user_global_id": user_global_id},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise SystemExit(f"F1 Fantasy API returned errors for your team: {data['errors']}")
    return data.get("pickedTeam") or data


BUDGET_KEY_HINTS = ("budget", "bank", "fund")
TRANSFER_KEY_HINTS = ("transfer",)
BOOST_KEY_HINTS = ("boost", "drs")


def extract_team_meta(picked_team):
    """Best-effort pull of budget remaining / transfers remaining / which
    player has DRS Boost active off the picked-team response.

    None of these three fields' actual names are confirmed against a
    real F1 Fantasy account — the community API wrapper this script's
    auth flow and endpoints were sourced from doesn't expose them either
    (its Team/League wrapper classes are stubs). Rather than hardcode a
    guessed key and silently show a wrong or missing number, this scans
    the top-level response for keys that merely *look* relevant and
    reports what it finds — including printing every top-level key seen
    to the run log unconditionally, so a look at one real run's log is
    enough to swap in the exact key name here if these guesses miss.
    """
    print(f"[team meta] top-level picked_team keys: {sorted(picked_team.keys())}")
    meta = {"budget": None, "transfers": None, "boosted_player_id": None}
    for key, value in picked_team.items():
        lower = key.lower()
        if meta["budget"] is None and any(h in lower for h in BUDGET_KEY_HINTS) and isinstance(value, (int, float)):
            meta["budget"] = value
        if meta["transfers"] is None and any(h in lower for h in TRANSFER_KEY_HINTS) and isinstance(value, (int, float)):
            meta["transfers"] = value

    for p in picked_team.get("picked_players") or picked_team.get("pickedPlayers") or []:
        for key, value in p.items():
            if any(h in key.lower() for h in BOOST_KEY_HINTS) and value:
                meta["boosted_player_id"] = p.get("player_id") or p.get("id")
    return meta


# ---- Jolpica-F1 (calendar, results, standings) ----

def jolpica_get(path):
    resp = requests.get(f"{JOLPICA_BASE}/{path}", headers=JOLPICA_HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()["MRData"]


def get_next_race():
    races = jolpica_get("current/next.json")["RaceTable"]["Races"]
    return races[0] if races else None


def get_last_results():
    races = jolpica_get("current/last/results.json")["RaceTable"]["Races"]
    return races[0] if races else None


def get_last_qualifying():
    races = jolpica_get("current/last/qualifying.json")["RaceTable"]["Races"]
    return races[0] if races else None


def get_driver_standings():
    lists = jolpica_get("current/driverStandings.json")["StandingsTable"]["StandingsLists"]
    return lists[0]["DriverStandings"] if lists else []


def parse_session_datetime(date_str, time_str):
    if not date_str:
        return None
    if time_str:
        try:
            return datetime.fromisoformat(f"{date_str}T{time_str.replace('Z', '+00:00')}")
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---- Weather ----

def fetch_weather(lat, lon, date):
    key = (round(float(lat), 2), round(float(lon), 2), date.isoformat())
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
        result = (wind_list[0] if wind_list else None, precip_list[0] if precip_list else None)
    except Exception:
        result = None
    _weather_cache[key] = result
    return result


def weather_line(lat, lon, date, label):
    forecast = fetch_weather(lat, lon, date)
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
    return f"⚠️ {label}: {', '.join(flags)}"


# ---- AI reasoning (Groq, optional — same setup as the ESPN/Sleeper versions) ----

def call_ai_model(prompt):
    if not GROQ_API_KEY:
        print("AI reasoning skipped: GROQ_API_KEY is not set.")
        return None
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
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
            print(f"AI reasoning call returned empty content. finish_reason={choice.get('finish_reason')!r}")
            return None
        return content
    except Exception as e:
        print(f"AI reasoning call failed with an unexpected error: {e}")
        return None


def driver_recent_form(driver_id, last_results, last_qualifying):
    """Plain-language recent form for one driver, from the last
    completed race + qualifying — not a multi-race average, since
    Jolpica's per-driver history would mean one call per driver per
    lookback week. Good enough for a single DRS Boost / value call,
    which only needs "did this driver just look sharp or not"."""
    bits = []
    if last_results:
        for r in last_results.get("Results", []):
            if r["Driver"]["driverId"] == driver_id:
                bits.append(f"finished P{r['position']} in the last race (status: {r.get('status', '?')})")
                break
    if last_qualifying:
        for r in last_qualifying.get("QualifyingResults", []):
            if r["Driver"]["driverId"] == driver_id:
                bits.append(f"qualified P{r['position']} last time out")
                break
    return "; ".join(bits) or "no recent race data available"


def ai_drs_boost_reasoning(candidate_name, candidate_form, alternatives_summary):
    prompt = (
        "You're helping an F1 Fantasy manager pick which of their 5 drivers "
        "to put DRS Boost on (doubles that driver's points for the race). "
        "Using ONLY the facts below, write 1-2 concise, plain-spoken "
        "sentences on whether this pick makes sense.\n\n"
        f"Suggested pick: {candidate_name} — {candidate_form}.\n"
        f"Other drivers on the team: {alternatives_summary}."
    )
    return call_ai_model(prompt) or (
        f"{candidate_name} has the strongest recent form on your team, so it's the rule-based pick for DRS Boost."
    )


def ai_transfer_reasoning(out_name, out_form, in_name, in_form, price_note):
    prompt = (
        "You're helping an F1 Fantasy manager decide on a possible driver "
        "transfer. Using ONLY the facts below — don't invent anything else "
        "— write 1-2 concise sentences on whether this swap looks worth it.\n\n"
        f"Current pick: {out_name} — {out_form}.\n"
        f"Candidate: {in_name} — {in_form}. {price_note}."
    )
    return call_ai_model(prompt) or (
        f"{in_name} has been in better recent form than {out_name} for a similar price — worth a look, "
        "but weigh it against your transfers-remaining count above."
    )


# ---- Report ----

def build_report(session):
    next_race = get_next_race()
    if not next_race:
        return None, "No upcoming race found on the F1 calendar (likely off-season)."

    quali = next_race.get("Qualifying") or {}
    quali_dt = parse_session_datetime(quali.get("date"), quali.get("time"))
    race_dt = parse_session_datetime(next_race.get("date"), next_race.get("time"))
    deadline_dt = quali_dt or race_dt
    now = datetime.now(timezone.utc)

    if deadline_dt is None:
        return None, "Could not determine a qualifying/race time for the next round — skipping."
    if deadline_dt < now:
        return None, f"Next round's deadline ({deadline_dt.isoformat()}) has already passed — skipping."
    if deadline_dt - now > timedelta(days=DEADLINE_WINDOW_DAYS):
        return None, (
            f"Next qualifying/deadline is {deadline_dt.isoformat()}, more than "
            f"{DEADLINE_WINDOW_DAYS} day(s) away — skipping until it's closer."
        )

    circuit = next_race.get("Circuit", {})
    location = circuit.get("Location", {})
    race_name = next_race.get("raceName", "Unknown Grand Prix")

    user_id = get_current_user(session)
    player_pool = get_player_pool(session)
    picked_team = get_picked_team(session, user_id, TEAM_SLOT)
    meta = extract_team_meta(picked_team)

    picks = picked_team.get("picked_players") or picked_team.get("pickedPlayers") or []
    resolved = []
    for pick in picks:
        pid = pick.get("player_id") or pick.get("id")
        info = player_pool.get(pid, {})
        resolved.append({
            "id": pid,
            "name": info.get("display_name") or info.get("full_name") or info.get("name") or f"Unknown player {pid}",
            "kind": (info.get("position_id") or info.get("position") or info.get("role") or "?"),
            "price": info.get("price") or info.get("value"),
            "raw": info,
        })

    last_results = get_last_results()
    last_qualifying = get_last_qualifying()
    driver_standings = get_driver_standings()
    standings_by_name = {}
    for s in driver_standings:
        full = f"{s['Driver']['givenName']} {s['Driver']['familyName']}"
        standings_by_name[full.lower()] = s

    parts = [
        f"## {race_name} — team check",
        f"**Circuit:** {circuit.get('circuitName', '?')}, {location.get('locality', '?')}, {location.get('country', '?')}",
        f"**Qualifying deadline:** {quali_dt.isoformat() if quali_dt else 'unknown — check the F1 Fantasy app'}",
    ]
    if meta["budget"] is not None:
        parts.append(f"**Budget remaining:** {meta['budget']}")
    if meta["transfers"] is not None:
        parts.append(f"**Transfers remaining:** {meta['transfers']}")
    parts.append("")

    if location.get("lat") and location.get("long"):
        w_lines = []
        if quali_dt:
            w = weather_line(location["lat"], location["long"], quali_dt.date(), "Qualifying")
            if w:
                w_lines.append(w)
        if race_dt:
            w = weather_line(location["lat"], location["long"], race_dt.date(), "Race")
            if w:
                w_lines.append(w)
        parts.append("### ⚠️ Weather")
        parts.append("\n".join(w_lines) if w_lines else "No notable wind/rain in the forecast yet.")
        parts.append("")

    parts.append("### Your team")
    if resolved:
        lines = ["| Player | Type | Price | DRS Boost |", "|---|---|---|---|"]
        for p in resolved:
            boost = "✅" if meta["boosted_player_id"] and p["id"] == meta["boosted_player_id"] else ""
            price = f"${p['price']}M" if isinstance(p["price"], (int, float)) else "?"
            lines.append(f"| {p['name']} | {p['kind']} | {price} | {boost} |")
        parts.append("\n".join(lines))
    else:
        parts.append(
            "Couldn't resolve your picked players — check the run log for the raw picked_teams response "
            "and confirm F1_SESSION_COOKIE hasn't expired."
        )
    parts.append("")

    # DRS Boost suggestion — rule-based: whichever of your drivers had the
    # best recent race finish, unless that's already who's boosted.
    driver_picks = [p for p in resolved if isinstance(p["kind"], str) and "constructor" not in p["kind"].lower()]
    if driver_picks and last_results:
        finish_pos = {}
        for r in last_results.get("Results", []):
            driver_full = f"{r['Driver']['givenName']} {r['Driver']['familyName']}".lower()
            for p in driver_picks:
                if p["name"].lower() == driver_full:
                    try:
                        finish_pos[p["id"]] = int(r["position"])
                    except (ValueError, TypeError):
                        pass
        if finish_pos:
            best_id = min(finish_pos, key=finish_pos.get)
            best = next(p for p in driver_picks if p["id"] == best_id)
            already_boosted = meta["boosted_player_id"] == best_id
            parts.append("### \U0001F680 DRS Boost suggestion")
            if already_boosted:
                parts.append(f"You've already got DRS Boost on **{best['name']}** — matches the rule-based pick.")
            else:
                driver_id_guess = best["name"].lower().replace(" ", "_")
                form = driver_recent_form(driver_id_guess, last_results, last_qualifying)
                others = ", ".join(p["name"] for p in driver_picks if p["id"] != best_id)
                reasoning = ai_drs_boost_reasoning(best["name"], form, others)
                parts.append(f"Consider **{best['name']}** — {form}.")
                parts.append(reasoning)
            parts.append("")

    parts.append("### \U0001F3C6 League standings")
    parts.append(
        f"https://fantasy.formula1.com/en/leaderboard/private?leagueId={F1_LEAGUE_ID} "
        "(pulled live in-app — F1's public leaderboard feed doesn't include team picks, "
        "just cumulative scores, so it's linked here rather than duplicated as a stale table)."
    )

    return "\n".join(parts), None


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


def issue_already_open(title):
    """Best-effort dedupe: since this script is meant to run on a daily
    cron and stays 'live' for DEADLINE_WINDOW_DAYS days before a race, a
    naive daily post would open several near-duplicate Issues per race
    weekend. Skips posting if an open Issue with this exact title already
    exists. Best-effort — a lookup failure just means a possible
    duplicate post, not a crash."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GH_REPOSITORY}/issues",
            headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"},
            params={"state": "open", "per_page": 20},
            timeout=20,
        )
        resp.raise_for_status()
        return any(issue["title"] == title for issue in resp.json())
    except Exception:
        return False


def main():
    session = fantasy_session()
    report, skip_reason = build_report(session)
    if report is None:
        print(skip_reason)
        return

    print(report)  # also shows up in the GitHub Actions run log
    race_name = re.search(r"^## (.+?) — team check", report).group(1)
    title = f"F1 Fantasy team check — {race_name}"
    if issue_already_open(title):
        print(f"Issue {title!r} is already open — not posting again.")
        return
    create_github_issue(title, report)


if __name__ == "__main__":
    main()
