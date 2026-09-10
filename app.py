"""
app.py

Streamlit front end for the Render <-> Calico Tabbycat sync tool.
Wraps sync_core.py (ported from Render_Calico_Sync_v2.ipynb) so
non-technical tab directors can run the per-round workflow without
touching code.

Run locally:   streamlit run app.py
On Render:     started via render.yaml's startCommand
"""

import streamlit as st
import sync_core as sc

st.set_page_config(page_title="Render <-> Calico Sync", layout="centered")

st.title("Render ↔ Calico Tabbycat Sync")
st.caption("IDL per-round draw/results sync tool")

try:
    sc.load_config()
except sc.ConfigError as e:
    st.error(
        f"{e}\n\nSet RENDER_URL, RENDER_TOKEN, CALICO_URL, CALICO_TOKEN as "
        f"Environment Variables on your Render service (Dashboard -> your "
        f"service -> Environment), then redeploy."
    )
    st.stop()

section = st.sidebar.radio(
    "Section",
    ["1. Import Teams", "2. Pull Results → Render", "3. Review Pause", "4. Push Draw → Calico"],
)

# ---------------------------------------------------------------------------
# Section 1
# ---------------------------------------------------------------------------
if section == "1. Import Teams":
    st.header("Section 1 — One-time Team Import")
    st.warning("Run this once only, before Round 1. Re-running will create duplicate teams.")

    if st.button("Run Team Import", type="primary"):
        progress = st.progress(0.0)
        status = st.empty()

        def cb(done, total, name):
            progress.progress(done / total)
            status.write(f"Created team {done}/{total}: {name}")

        try:
            team_map = sc.import_teams(progress_callback=cb)
            st.success(f"Imported {len(team_map)} teams. team_map.json written.")
        except Exception as e:
            st.error(f"Import failed: {e}")

# ---------------------------------------------------------------------------
# Section 2
# ---------------------------------------------------------------------------
elif section == "2. Pull Results → Render":
    st.header("Section 2 — Pull Confirmed Calico Results → Write to Render")

    round_seq = st.number_input("Round number", min_value=1, step=1, value=1)

    if st.button("Pull & Write Results", type="primary"):
        progress = st.empty()
        status = st.empty()

        def cb(done, total):
            status.write(f"Written {done}/{total} results")

        try:
            team_map = sc.load_team_map()
            if not team_map:
                st.error("team_map.json not found — run Section 1 first.")
                st.stop()
            # team_map.json keys were saved as Calico team ids (ints via json.dump);
            # JSON always round-trips dict keys as strings, so cast back to int.
            team_map = {int(k): v for k, v in team_map.items()}

            render_lookup = sc.build_render_pairing_lookup(int(round_seq))
            written, skipped = sc.write_results_to_render(
                int(round_seq), team_map, render_lookup, progress_callback=cb
            )
            st.success(f"Wrote {len(written)} result(s) to Render.")
            if skipped:
                st.info(f"Skipped (no confirmed ballot or no matching Render pairing): {skipped}")
        except Exception as e:
            st.error(f"Failed: {e}")

# ---------------------------------------------------------------------------
# Section 3
# ---------------------------------------------------------------------------
elif section == "3. Review Pause":
    st.header("Section 3 — Manual Review")
    st.markdown(
        "Go to **Render's Tabbycat UI**, generate the draw for the next round, "
        "and review it there (adjudicator allocation, venues, etc. are **not synced** "
        "by this tool — assign them on Render or directly on Calico afterward).\n\n"
        "Once you're happy with it, tick the box below. Section 4 won't be usable "
        "until you do."
    )
    reviewed = st.checkbox("I've generated and reviewed the draw on Render's UI ✅")
    st.session_state["reviewed"] = reviewed
    if reviewed:
        st.success("Review confirmed. You can proceed to Section 4.")
    else:
        st.info("Waiting for review confirmation.")

# ---------------------------------------------------------------------------
# Section 4
# ---------------------------------------------------------------------------
elif section == "4. Push Draw → Calico":
    st.header("Section 4 — Pull Render's Draw → Push to Calico")

    if not st.session_state.get("reviewed"):
        st.warning("Please complete Section 3 (Review Pause) first.")
        st.stop()

    round_seq = st.number_input("Round number", min_value=1, step=1, value=1, key="push_round")
    mark_as_draft = st.checkbox(
        "Mark round as Draft on Calico after pushing (never Released)",
        value=True,
    )

    if st.button("Push Draw to Calico", type="primary"):
        status = st.empty()

        def cb(done, total):
            status.write(f"Pushed {done}/{total} pairings")

        try:
            team_map = sc.load_team_map()
            if not team_map:
                st.error("team_map.json not found — run Section 1 first.")
                st.stop()
            team_map = {int(k): v for k, v in team_map.items()}

            result = sc.push_draw_to_calico(
                int(round_seq), team_map, mark_as_draft=mark_as_draft, progress_callback=cb
            )
            st.success(f"Pushed {len(result['pushed'])} pairing(s) to Calico.")
            if mark_as_draft:
                st.info("Round marked as Draft on Calico — release it manually when ready.")
            else:
                st.info("MARK_AS_DRAFT was off — set draw_status on Calico manually if needed.")
        except Exception as e:
            st.error(f"Failed: {e}")
