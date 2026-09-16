import os
import json
import requests

TEAM_MAP_PATH = "team_map.json"
REQUEST_TIMEOUT = 30  # seconds - prevents an indefinite hang on a cold/hung instance


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
    url = raw_url.strip().rstrip("/")

    if "/api/v1/tournaments/" in url:
        return url

    host, _, slug = url.rpartition("/")
    if not host or not slug:
        raise ConfigError(
            f"Could not parse tournament URL: '{raw_url}'. Expected either "
            f"'https://host/slug' or 'https://host/api/v1/tournaments/slug'."
        )
    return f"{host}/api/v1/tournaments/{slug}"


def load_config():
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
    return isinstance(data, dict) and "results" in data and "next" in data


def _get_json_following_pagination(url, headers):
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


def render_put(path, payload):
    r = requests.put(f"{RENDER_URL}{path}", headers=_headers(RENDER_TOKEN), json=payload, timeout=REQUEST_TIMEOUT)
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


def load_team_map() -> dict:
    if not os.path.exists(TEAM_MAP_PATH):
        return {}
    with open(TEAM_MAP_PATH) as f:
        return json.load(f)


def save_team_map(team_map: dict):
    with open(TEAM_MAP_PATH, "w") as f:
        json.dump(team_map, f, indent=2)


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
    return {t["reference"]: t["id"] for t in render_get("/teams")}


def check_team_import_readiness():
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


_render_team_speakers_cache = {}


def get_render_team_speakers(render_team_id):
    if render_team_id not in _render_team_speakers_cache:
        team = render_get(f"/teams/{render_team_id}")
        _render_team_speakers_cache[render_team_id] = team["speakers"]
    return _render_team_speakers_cache[render_team_id]


def extract_calico_team_id(team_field):
    if isinstance(team_field, int):
        return team_field
    return int(str(team_field).rstrip("/").split("/")[-1])


def get_calico_pairings(round_seq):
    return calico_get(f"/rounds/{round_seq}/pairings")


def get_confirmed_ballot(round_seq, pairing_id):
    ballots = calico_get(f"/rounds/{round_seq}/pairings/{pairing_id}/ballots")
    confirmed = [b for b in ballots if b.get("confirmed")]
    if not confirmed:
        return None
    return max(confirmed, key=lambda b: b.get("version", 0))


def build_render_pairing_lookup(round_seq):
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


def get_calico_team_availability(round_seq):
    """
    List of Calico team detail URLs currently marked available for the round.
    Calico's ?teams=true filter is not reliably honored in practice — it can
    return adjudicators and venues mixed in. Filter client-side on URL shape
    so only actual team URLs ever reach the resolve/push step.
    """
    raw = calico_get(f"/rounds/{round_seq}/availabilities?teams=true")
    return [u for u in raw if "/teams/" in str(u)]


def check_availability_sync(round_seq, team_map):
    """
    Compare Calico's checked-in teams for a round against team_map.json.
    Never raises on an unmapped team — flags it as unresolved instead, same
    as Section 1's 'partial' state, since new teams may be added mid-tournament.
    """
    calico_urls = get_calico_team_availability(round_seq)
    calico_refs = get_calico_id_to_reference()

    resolved_render_urls = []
    resolved_refs = []
    unresolved_ids = []
    unresolved_refs = []

    for url in calico_urls:
        calico_id = extract_calico_team_id(url)
        render_id = team_map.get(calico_id)
        ref = calico_refs.get(calico_id, f"Team {calico_id}")
        if render_id is None:
            unresolved_ids.append(calico_id)
            unresolved_refs.append(ref)
        else:
            resolved_render_urls.append(f"{RENDER_URL}/teams/{render_id}")
            resolved_refs.append(ref)

    return {
        "total_available": len(calico_urls),
        "resolved_render_urls": resolved_render_urls,
        "resolved_refs": resolved_refs,
        "unresolved_ids": unresolved_ids,
        "unresolved_refs": unresolved_refs,
    }


def push_team_availability_to_render(round_seq, resolved_render_urls):
    """
    Writes team availability to Render for this round.

    Tabbycat's PUT /availabilities endpoint 500s if any team in the new list
    is already marked available (confirmed: happens even when the empty-list
    clear is scoped with ?teams=true first — the bug appears to be in
    Tabbycat itself, not something we can work around from this side).

    Workaround (manual, by design): clear all team availability on Render's
    own UI before running this sync. This function does a single plain PUT
    and assumes that's already been done.
    """
    return render_put(f"/rounds/{round_seq}/availabilities", resolved_render_urls)


def get_render_tournament_name():
    data = render_get("")
    return data.get("name") or data.get("short_name") or "(unnamed tournament)"


def get_calico_tournament_name():
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
    return {
        "calico_teams": get_calico_team_count(),
        "render_teams": get_render_team_count(),
        "calico_adjs": get_calico_adjudicator_count(),
        "render_adjs": get_render_adjudicator_count(),
    }


def count_render_pairings_with_results(round_seq):
    pairings = get_render_pairings(round_seq)
    return sum(1 for p in pairings if p.get("result_status") == "C")


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

STANDINGS_METRICS = ["points", "speaks_sum", "firsts", "seconds", "draw_strength"]


def _extract_id(url):
    return int(str(url).rstrip("/").split("/")[-1])


def get_render_standings():
    metrics = ",".join(STANDINGS_METRICS)
    return render_get(f"/teams/standings?metrics={metrics}")


def get_calico_standings():
    metrics = ",".join(STANDINGS_METRICS)
    return calico_get(f"/teams/standings?metrics={metrics}")


def get_calico_id_to_reference():
    return {t["id"]: t["reference"] for t in calico_get("/teams")}


def _metrics_dict(entry):
    return {m["metric"]: m["value"] for m in entry["metrics"]}


def compare_standings():
    calico_standings = get_calico_standings()
    render_standings = get_render_standings()
    team_map = {int(k): v for k, v in load_team_map().items()}
    calico_names = get_calico_id_to_reference()

    render_by_id = {_extract_id(e["team"]): e for e in render_standings}

    rows = []
    for entry in calico_standings:
        calico_id = _extract_id(entry["team"])
        name = calico_names.get(calico_id, f"Team {calico_id}")
        render_id = team_map.get(calico_id)
        render_entry = render_by_id.get(render_id) if render_id is not None else None

        row = {"team": name, "calico_rank": entry["rank"]}

        if render_entry is None:
            row["render_rank"] = None
            row["status"] = "Missing on Render"
            for m in STANDINGS_METRICS:
                row[m] = f"{_metrics_dict(entry).get(m)} / —"
            rows.append(row)
            continue

        row["render_rank"] = render_entry["rank"]
        calico_metrics = _metrics_dict(entry)
        render_metrics = _metrics_dict(render_entry)

        mismatched = entry["rank"] != render_entry["rank"]
        for m in STANDINGS_METRICS:
            cv, rv = calico_metrics.get(m), render_metrics.get(m)
            if cv != rv:
                mismatched = True
                row[m] = f"{cv} / {rv}"
            else:
                row[m] = str(cv)

        row["status"] = "Mismatch" if mismatched else "Match"
        rows.append(row)

    matched_ids = {team_map.get(_extract_id(e["team"])) for e in calico_standings}
    for entry in render_standings:
        render_id = _extract_id(entry["team"])
        if render_id not in matched_ids:
            rows.append({
                "team": f"Render team {render_id}",
                "calico_rank": None,
                "render_rank": entry["rank"],
                "status": "Missing on Calico",
                **{m: f"— / {_metrics_dict(entry).get(m)}" for m in STANDINGS_METRICS},
            })

    all_match = all(r["status"] == "Match" for r in rows)
    return rows, all_match
