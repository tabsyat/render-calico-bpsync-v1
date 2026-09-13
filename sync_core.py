"""
sync_core.py

Core Render <-> Calico Tabbycat sync logic for IDL tournaments.
Ported line-for-line from the working Colab notebook
(Render_Calico_Sync_v2.ipynb)
"""

import os
import json
import requests

TEAM_MAP_PATH = "team_map.json"
REQUEST_TIMEOUT = 30  # seconds - prevents an indefinite hang on a cold/hung instance


# ---------------------------------------------------------------------------
# Section 0 - Setup
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    pass


def _get_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigError(
            f"Missing required environment variable: {name}. "
            f"Set it in your Render service's Environment settings."
        )
    return val


RENDER_URL = None
RENDER_TOKEN = None
CALICO_URL = None
CALICO_TOKEN = None


def normalize_tournament_api_url(raw_url: str) -> str:
    """
    Accepts any of the following, and returns the full API path form:
      1. https://host/api/v1/tournaments/slug        (already full - used as-is)
      2. https://host/slug                            (simple form - expanded)
      3. Either of the above with a trailing slash    (stripped first)

    This makes env var setup forgiving of pasting straight from a
    tournament's public URL bar, which always includes the trailing
    slash Tabbycat's site adds.
    """
    url = raw_url.strip().rstrip("/")

    if "/api/v1/tournaments/" in url:
        # Already the full API path form - use as-is.
        return url

    # Simple form: last path segment is the slug, everything before it
    # is the host. e.g. https://host.com/testbp -> host=https://host.com, slug=testbp
    host, _, slug = url.rpartition("/")
    if not host or not slug:
        raise ConfigError(
            f"Could not parse tournament URL: '{raw_url}'. Expected either "
            f"'https://host/slug' or 'https://host/api/v1/tournaments/slug'."
        )
    return f"{host}/api/v1/tournaments/{slug}"


def load_config():
    """Call once at app startup. Mirrors Section 0's userdata.get() calls."""
    global RENDER_URL, RENDER_TOKEN, CALICO_URL, CALICO_TOKEN
    RENDER_URL = normalize_tournament_api_url(_get_env("RENDER_URL"))
    RENDER_TOKEN = _get_env("RENDER_TOKEN")
    CALICO_URL = normalize_tournament_api_url(_get_env("CALICO_URL"))
    CALICO_TOKEN = _get_env("CALICO_TOKEN")


def _headers(token):
    return {"Authorization": f"Token {token}", "Content-Type": "application/json"}


def _raise_with_body(response):
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    response_error = requests.HTTPError(
        f"{response.status_code} {response.reason} for url: {response.url}\nResponse body: {detail}",
        response=response,
    )
    raise response_error


def _is_paginated_envelope(data):
    """
    DRF's paginated list response always has both 'results' and 'next'
    keys (next may be null on the last/only page). A single-object GET
    (e.g. /teams/{id}) never has this shape, so this check is safe to
    use as an auto-detect without touching any call sites.
    """
    return isinstance(data, dict) and "results" in data and "next" in data


def _get_json_following_pagination(url, headers):
    """
    Fetches url; if the response is a paginated DRF envelope, keeps
    following 'next' and concatenating 'results' until it's null.
    Otherwise returns the JSON body unchanged (plain list or single object).
    """
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        _raise_with_body(r)
    data = r.json()

    if not _is_paginated_envelope(data):
        return data

    all_results = list(data["results"])
    next_url = data.get("next")
    while next_url:
        r = requests.get(next_url, headers=headers, timeout=REQUEST_TIMEOUT)
        if not r.ok:
            _raise_with_body(r)
        page = r.json()
        all_results.extend(page.get("results", []))
        next_url = page.get("next")

    return all_results


def render_get(path):
    return _get_json_following_pagination(f"{RENDER_URL}{path}", _headers(RENDER_TOKEN))


def render_post(path, payload):
    r = requests.post(f"{RENDER_URL}{path}", headers=_headers(RENDER_TOKEN), json=payload, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        _raise_with_body(r)
    return r.json()


def render_patch(path, payload):
    r = requests.patch(f"{RENDER_URL}{path}", headers=_headers(RENDER_TOKEN), json=payload, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        _raise_with_body(r)
    return r.json()


def calico_get(path):
    return _get_json_following_pagination(f"{CALICO_URL}{path}", _headers(CALICO_TOKEN))


def calico_post(path, payload):
    r = requests.post(f"{CALICO_URL}{path}", headers=_headers(CALICO_TOKEN), json=payload, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        _raise_with_body(r)
    return r.json()


def calico_patch(path, payload):
    r = requests.patch(f"{CALICO_URL}{path}", headers=_headers(CALICO_TOKEN), json=payload, timeout=REQUEST_TIMEOUT)
    if not r.ok:
        _raise_with_body(r)
    return r.json()


# ---------------------------------------------------------------------------
# team_map.json persistence
# ---------------------------------------------------------------------------

def load_team_map() -> dict:
    if not os.path.exists(TEAM_MAP_PATH):
        return {}
    with open(TEAM_MAP_PATH) as f:
        return json.load(f)


def save_team_map(team_map: dict):
    with open(TEAM_MAP_PATH, "w") as f:
        json.dump(team_map, f, indent=2)


# ---------------------------------------------------------------------------
# Section 1 - one-time team import
# ---------------------------------------------------------------------------

def clean_speaker(sp):
    return {
        "name": sp.get("name"),
        "email": sp.get("email", ""),
        "phone": sp.get("phone", ""),
        "anonymous": sp.get("anonymous", False),
        "gender": sp.get("gender", ""),
        "pronoun": sp.get("pronoun", ""),
        "categories": [],
    }


def clean_team_payload(team):
    return {
        "reference": team.get("reference"),
        "short_reference": team.get("short_reference"),
        "long_name": team.get("long_name"),
        "code_name": team.get("code_name"),
        "use_institution_prefix": team.get("use_institution_prefix", False),
        "emoji": team.get("emoji"),
        "seed": team.get("seed"),
        "speakers": [clean_speaker(sp) for sp in team.get("speakers", [])],
    }


def import_teams(progress_callback=None, only_calico_ids=None):
    """
    Imports teams from Calico to Render. If only_calico_ids is given
    (a set/list of Calico team ids), only those teams are imported -
    used to resume a partial import without recreating teams that are
    already on Render. Merges into (rather than overwrites) any
    existing team_map.json.
    """
    calico_teams = calico_get("/teams")
    if only_calico_ids is not None:
        only_calico_ids = set(only_calico_ids)
        calico_teams = [t for t in calico_teams if t["id"] in only_calico_ids]

    team_map = {int(k): v for k, v in load_team_map().items()}
    for i, team in enumerate(calico_teams):
        payload = clean_team_payload(team)
        created = render_post("/teams", payload)
        team_map[team["id"]] = created["id"]
        if progress_callback:
            progress_callback(i + 1, len(calico_teams), team.get("reference", ""))
    save_team_map(team_map)
    return team_map


def get_render_team_references():
    """reference -> render team id, for identity-based matching."""
    return {t["reference"]: t["id"] for t in render_get("/teams")}


def check_team_import_readiness():
    """
    Identity-based (not count-only) readiness check: compares actual
    team references between Calico and Render, so a coincidental count
    match isn't mistaken for "already imported", and a partial import
    can be resumed by reference rather than guessed at.

    Returns (calico_count, render_count, status, info) where info is:
      - for "partial": the list of Calico teams (full dicts) still
        missing on Render, so the caller can pass their ids straight
        into import_teams(only_calico_ids=...)
      - otherwise: a message string
    """
    calico_teams = calico_get("/teams")
    calico_refs = {t["reference"]: t for t in calico_teams}
    render_refs = get_render_team_references()

    x = len(calico_refs)
    y = len(render_refs)

    if x == 0:
        return x, y, "empty_source", "Calico has no teams yet — importing would copy nothing."

    missing = [t for ref, t in calico_refs.items() if ref not in render_refs]

    if y == 0:
        return x, y, "ready", "Teams are ready to copy!"
    if not missing:
        return x, y, "duplicate_risk", "Teams already exist on Render — please double check before importing again."
    return x, y, "partial", missing


# ---------------------------------------------------------------------------
# Section 2 - pull confirmed Calico results -> write to Render
# ---------------------------------------------------------------------------

_render_team_speakers_cache = {}


def get_render_team_speakers(render_team_id):
    if render_team_id not in _render_team_speakers_cache:
        team = render_get(f"/teams/{render_team_id}")
        _render_team_speakers_cache[render_team_id] = team["speakers"]
    return _render_team_speakers_cache[render_team_id]


# --- RECONSTRUCTED: not defined in the exported notebook cells ---
def extract_calico_team_id(team_field):
    """
    Calico pairing['teams'][i]['team'] may be a full URL or a bare id
    depending on endpoint. Handles both.
    """
    if isinstance(team_field, int):
        return team_field
    return int(str(team_field).rstrip("/").split("/")[-1])


# --- RECONSTRUCTED: not defined in the exported notebook cells ---
def get_calico_pairings(round_seq):
    return calico_get(f"/rounds/{round_seq}/pairings")


# --- RECONSTRUCTED: not defined in the exported notebook cells ---
def get_confirmed_ballot(round_seq, pairing_id):
    """
    Fetches ballots for a pairing and returns the highest-version
    confirmed one, or None if there isn't one yet.
    """
    ballots = calico_get(f"/rounds/{round_seq}/pairings/{pairing_id}/ballots")
    confirmed = [b for b in ballots if b.get("confirmed")]
    if not confirmed:
        return None
    return max(confirmed, key=lambda b: b.get("version", 0))


# --- RECONSTRUCTED: not defined in the exported notebook cells ---
def build_render_pairing_lookup(round_seq):
    """
    Maps frozenset(render_team_ids) -> render_pairing_id, so a Calico
    pairing can be matched to its Render counterpart by team set rather
    than by pairing ID (which won't match across the two instances).
    """
    render_pairings = get_render_pairings(round_seq)
    lookup = {}
    for rp in render_pairings:
        team_ids = frozenset(
            int(t["team"].rstrip("/").split("/")[-1]) for t in rp["teams"]
        )
        lookup[team_ids] = rp["id"]
    return lookup


def write_results_to_render(round_seq, team_map, render_lookup, progress_callback=None):
    calico_pairings = get_calico_pairings(round_seq)
    skipped = []
    written = []

    for pairing in calico_pairings:
        ballot = get_confirmed_ballot(round_seq, pairing["id"])
        if ballot is None:
            skipped.append(pairing["id"])
            continue

        calico_team_ids = frozenset(extract_calico_team_id(t["team"]) for t in pairing["teams"])
        render_team_ids = frozenset(team_map[tid] for tid in calico_team_ids)
        render_pairing_id = render_lookup.get(render_team_ids)
        if render_pairing_id is None:
            skipped.append(pairing["id"])
            continue

        sheet = ballot["result"]["sheets"][0]
        remapped_teams = []
        for t in sheet["teams"]:
            calico_tid = extract_calico_team_id(t["team"])
            render_tid = team_map[calico_tid]
            render_speakers = get_render_team_speakers(render_tid)

            speeches = []
            for i, sp in enumerate(t.get("speeches", [])):
                if i >= len(render_speakers):
                    break  # more Calico speeches than Render speakers; drop extras
                speeches.append({
                    "ghost": sp.get("ghost", False),
                    "score": sp["score"],
                    "speaker": render_speakers[i]["url"],
                })

            remapped_teams.append({
                "side": t["side"],
                "points": t["points"],
                "win": t["win"],
                "score": t["score"],
                "team": f"{RENDER_URL}/teams/{render_tid}",
                "speeches": speeches,
            })

        payload = {
            "result": {"sheets": [{"teams": remapped_teams}]},
            "confirmed": True,
        }
        r = requests.post(
            f"{RENDER_URL}/rounds/{round_seq}/pairings/{render_pairing_id}/ballots",
            headers=_headers(RENDER_TOKEN), json=payload, timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            _raise_with_body(r)
        written.append(render_pairing_id)

        if progress_callback:
            progress_callback(len(written), len(calico_pairings))

    return written, skipped


# ---------------------------------------------------------------------------
# Section 4 - pull Render's draw -> push to Calico
# ---------------------------------------------------------------------------

def get_render_pairings(round_seq):
    return render_get(f"/rounds/{round_seq}/pairings")


def get_calico_existing_pairings(round_seq):
    return calico_get(f"/rounds/{round_seq}/pairings")


def build_calico_payload(render_pairing, inverse_team_map):
    teams = []
    for t in render_pairing["teams"]:
        render_tid = int(t["team"].rstrip("/").split("/")[-1])
        calico_tid = inverse_team_map[render_tid]
        teams.append({"team": f"{CALICO_URL}/teams/{calico_tid}", "side": t["side"]})
    return {
        "bracket": render_pairing.get("bracket", 0),
        "room_rank": render_pairing.get("room_rank", 0),
        "teams": teams,
    }


def push_pairings_to_calico(round_seq, render_pairings, inverse_team_map, progress_callback=None):
    pushed = []
    for rp in render_pairings:
        payload = build_calico_payload(rp, inverse_team_map)
        created = requests.post(
            f"{CALICO_URL}/rounds/{round_seq}/pairings",
            headers=_headers(CALICO_TOKEN),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if not created.ok:
            _raise_with_body(created)
        pushed.append(created.json())
        if progress_callback:
            progress_callback(len(pushed), len(render_pairings))
    return pushed


def mark_calico_draft(round_seq):
    return calico_patch(f"/rounds/{round_seq}", {"draw_status": "D"})


def push_draw_to_calico(round_seq, team_map, mark_as_draft=True, progress_callback=None):
    """
    High-level Section 4 entry point for the app: refuses to double-post,
    pushes all pairings, optionally marks Draft. Never sets 'R' (Released).
    """
    existing = get_calico_existing_pairings(round_seq)
    if existing:
        raise RuntimeError(
            f"Calico already has {len(existing)} pairing(s) for round {round_seq} — "
            f"refusing to double-post. Delete them on Calico first if you really want to re-push."
        )

    render_pairings = get_render_pairings(round_seq)
    inverse_team_map = {v: k for k, v in team_map.items()}
    pushed = push_pairings_to_calico(round_seq, render_pairings, inverse_team_map, progress_callback)

    if mark_as_draft:
        mark_calico_draft(round_seq)

    return {"pushed": pushed}


# ---------------------------------------------------------------------------
# Section 0 - overview / reference counts, and shared duplicate-check helpers
# ---------------------------------------------------------------------------

def get_render_tournament_name():
    """RENDER_URL already points at the tournament root - GET it directly."""
    data = render_get("")
    return data.get("name") or data.get("short_name") or "(unnamed tournament)"


def get_calico_tournament_name():
    """CALICO_URL already points at the tournament root - GET it directly."""
    data = calico_get("")
    return data.get("name") or data.get("short_name") or "(unnamed tournament)"


def get_calico_team_count():
    return len(calico_get("/teams"))


def get_render_team_count():
    return len(render_get("/teams"))


def get_calico_adjudicator_count():
    return len(calico_get("/adjudicators"))


def get_render_adjudicator_count():
    return len(render_get("/adjudicators"))


def get_overview_counts():
    """Used by Section 0's Sync button - a reference dashboard, not a gate."""
    return {
        "calico_teams": get_calico_team_count(),
        "render_teams": get_render_team_count(),
        "calico_adjs": get_calico_adjudicator_count(),
        "render_adjs": get_render_adjudicator_count(),
    }
    return x, y, "partial", (
        "Some teams are not migrated to Render — please delete all teams on Render "
        "and re-run the import."
    )


def count_render_pairings_with_results(round_seq):

    pairings = get_render_pairings(round_seq)
    return sum(1 for p in pairings if p.get("result_status") == "C")


# ---------------------------------------------------------------------------
# Dummy adjudicator / venue fill-in (Section 0)
# ---------------------------------------------------------------------------

import math


def required_adj_and_room_count():

    calico_teams = get_calico_team_count()
    return math.ceil(calico_teams / 4) if calico_teams else 0


def get_render_venue_count():
    return len(render_get("/venues"))


def create_dummy_venues(target_count, progress_callback=None):

    existing = get_render_venue_count()
    to_create = max(target_count - existing, 0)

    created = []
    for i in range(to_create):
        n = existing + i + 1
        payload = {"name": f"Room {n}", "priority": n, "categories": []}
        result = render_post("/venues", payload)
        created.append(result)
        if progress_callback:
            progress_callback(i + 1, to_create, f"Room {n}")

    return {"target": target_count, "existing_before": existing, "created": len(created)}


def create_dummy_adjudicators(target_count, progress_callback=None):

    existing = get_render_adjudicator_count()
    to_create = max(target_count - existing, 0)

    created = []
    for i in range(to_create):
        n = existing + i + 1
        payload = {
            "name": f"Adj {n}",
            "base_score": 7,
            "trainee": False,
            "institution": None,
            "institution_conflicts": [],
            "team_conflicts": [],
            "adjudicator_conflicts": [],
        }
        result = render_post("/adjudicators", payload)
        created.append(result)
        if progress_callback:
            progress_callback(i + 1, to_create, f"Adj {n}")

    return {"target": target_count, "existing_before": existing, "created": len(created)}
