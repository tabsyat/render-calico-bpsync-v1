"""
app.py

Streamlit front end for the Render <-> Calico Tabbycat sync tool.
Wraps sync_core.py (ported from Render_Calico_Sync_v2.ipynb).

Sections are numbered by their actual position in the flow, not the
original notebook's numbering:
    0. Overview          - reference dashboard only, not a gate
    1. Import Teams       - once, before Round 1
    2. Review Pause         -\
    3. Push Draw -> Calico     > repeats every round, in this order
    4. Pull Results -> Render -/

Run locally:   streamlit run app.py
On Render:     started via render.yaml's startCommand
"""

import streamlit as st
import sync_core as sc

st.set_page_config(page_title="Render <-> Calico Sync", layout="centered")

# Streamlit doesn't expose an official API to remove individual entries
# (Print, Record a screencast) from the top-right "..." menu - only whole
# categories via menu_items. This CSS hides the whole menu button as the
# only reliable way to drop those two; Rerun/Settings go with it.
# If you'd rather keep Rerun/Settings and live with Print/Record, delete
# this block.
st.markdown("<style>#MainMenu {visibility: hidden;}</style>", unsafe_allow_html=True)

st.title("Render ↔ Calico Tabbycat Sync")

try:
    sc.load_config()
except sc.ConfigError as e:
    st.error(
        f"{e}\n\nSet RENDER_URL, RENDER_TOKEN, CALICO_URL, CALICO_TOKEN as "
        f"Environment Variables on your Render service (Dashboard -> your "
        f"service -> Environment), then redeploy."
    )
    st.stop()

SECTION_DESCRIPTIONS = {
    "0": "Reference dashboard — tournament info and current counts. Not required, just a sanity check.",
    "1": "One-time: copy all teams from Calico to Render, before Round 1.",
    "2": "Confirm you've reviewed the generated draw on Render's UI.",
    "3": "Push the reviewed draw from Render to Calico.",
    "4": "After a round is played on Calico, pull confirmed results into Render.",
}

section = st.sidebar.radio(
    "Section",
    ["0", "1", "2", "3", "4"],
    format_func=lambda s: {
        "0": "0. Overview",
        "1": "1. Import Teams",
        "2": "2. Review Pause",
        "3": "3. Push Draw → Calico",
        "4": "4. Pull Results → Render",
    }[s],
)

# ---------------------------------------------------------------------------
# Section 0 - Overview (reference only, never gates anything)
# ---------------------------------------------------------------------------
if section == "0":
    st.header("Section 0 — Overview")
    st.caption(SECTION_DESCRIPTIONS["0"])
    st.markdown(
        f"**Render instance:** `{sc.RENDER_URL}`\n\n"
        f"**Calico instance:** `{sc.CALICO_URL}`"
    )
    st.caption(
        "This is a reference dashboard only — it doesn't block or gate any "
        "other section. Section 1's import button always runs its own "
        "live check regardless of what's shown here."
    )

    if st.button("Sync counts", type="primary"):
        with st.spinner("Fetching counts..."):
            try:
                counts = sc.get_overview_counts()
                st.session_state["overview_counts"] = counts
            except Exception as e:
                st.error(f"Failed to fetch counts: {e}")

    counts = st.session_state.get("overview_counts")
    if counts:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Calico teams", counts["calico_teams"])
            st.metric("Calico adjudicators", counts["calico_adjs"])
        with col2:
            st.metric("Render teams", counts["render_teams"])
            st.metric("Render adjudicators", counts["render_adjs"])
    else:
        st.info("Click 'Sync counts' to fetch current numbers from both instances.")

    st.divider()
    st.subheader("Add dummy adjudicators & rooms")
    st.caption(
        "Enter how many adjudicators/rooms you need in total (e.g. from your own "
        "BP math — 1 per 4 teams, rounded up for byes/swings). Only fills the "
        "shortfall against what's already on Render — safe to click repeatedly."
    )

    col_adj, col_room = st.columns(2)

    with col_adj:
        st.markdown("**Adjudicators**")
        target_adj = st.number_input("Total adjudicators needed", min_value=0, step=1, key="target_adj")
        if st.button("Fill missing adjudicators"):
            with st.spinner("Checking and creating adjudicators..."):
                status_line = st.empty()

                def cb(done, total, name):
                    status_line.write(f"Created {done}/{total}: {name}")

                try:
                    result = sc.create_dummy_adjudicators(int(target_adj), progress_callback=cb)
                    if result["created"] == 0:
                        st.success(
                            f"Already have {result['existing_before']} — "
                            f"{result['target']} requested. Nothing to add."
                        )
                    else:
                        st.success(
                            f"Created {result['created']} adjudicator(s) "
                            f"({result['existing_before']} → {result['existing_before'] + result['created']}, "
                            f"{result['target']} requested)."
                        )
                except Exception as e:
                    st.error(f"Failed: {e}")

    with col_room:
        st.markdown("**Rooms**")
        target_room = st.number_input("Total rooms needed", min_value=0, step=1, key="target_room")
        if st.button("Fill missing rooms"):
            with st.spinner("Checking and creating rooms..."):
                status_line = st.empty()

                def cb(done, total, name):
                    status_line.write(f"Created {done}/{total}: {name}")

                try:
                    result = sc.create_dummy_venues(int(target_room), progress_callback=cb)
                    if result["created"] == 0:
                        st.success(
                            f"Already have {result['existing_before']} — "
                            f"{result['target']} requested. Nothing to add."
                        )
                    else:
                        st.success(
                            f"Created {result['created']} room(s) "
                            f"({result['existing_before']} → {result['existing_before'] + result['created']}, "
                            f"{result['target']} requested)."
                        )
                except Exception as e:
                    st.error(f"Failed: {e}")

# ---------------------------------------------------------------------------
# Section 1 - Import Teams (mandatory live check, but never blocks the button -
# warnings are advisory and can be overridden)
# ---------------------------------------------------------------------------
elif section == "1":
    st.header("Section 1 — One-time Team Import")
    st.caption(SECTION_DESCRIPTIONS["1"])
    st.warning("Run this once only, before Round 1. Re-running will create duplicate teams.")

    @st.dialog("Team count check")
    def import_check_dialog():
        with st.spinner("Checking team counts on Calico and Render..."):
            try:
                x, y, status, message = sc.check_team_import_readiness()
            except Exception as e:
                st.error(f"Check failed: {e}")
                return

        st.markdown(f"**Calico's Teams = {x}**")
        st.markdown(f"**Render's Teams = {y}**")

        if status == "ready":
            st.success(message)
            button_label = "Proceed with Import"
        elif status == "empty_source":
            st.warning(message)
            button_label = "Override warning and go ahead with team import"
        elif status == "duplicate_risk":
            st.error(message)
            button_label = "Override warning and go ahead with team import"
        else:  # partial
            st.warning(message)
            button_label = "Override warning and go ahead with team import"

        if st.button(button_label, type="primary"):
            progress = st.progress(0.0)
            status_line = st.empty()

            def cb(done, total, name):
                progress.progress(done / total)
                status_line.write(f"Created team {done}/{total}: {name}")

            try:
                team_map = sc.import_teams(progress_callback=cb)
                st.success(f"Imported {len(team_map)} teams. team_map.json written.")
            except Exception as e:
                st.error(f"Import failed: {e}")

    if st.button("Run Team Import", type="primary"):
        import_check_dialog()

    st.caption("Clicking this always re-checks live counts first — go back to Section 0 "
               "any time to double check the numbers match your expectations.")

# ---------------------------------------------------------------------------
# Section 2 - Review Pause
# ---------------------------------------------------------------------------
elif section == "2":
    st.header("Section 2 — Manual Review")
    st.caption(SECTION_DESCRIPTIONS["2"])
    st.markdown(
        "Go to **Render's Tabbycat UI**, generate the draw for the next round, "
        "and review it there (adjudicator allocation, venues, etc. are **not synced** "
        "by this tool — assign them on Render or directly on Calico afterward).\n\n"
        "Once you're happy with it, tick the box below. Section 3 won't be usable "
        "until you do."
    )
    reviewed = st.checkbox("I've generated and reviewed the draw on Render's UI ✅")
    st.session_state["reviewed"] = reviewed
    if reviewed:
        st.success("Review confirmed. You can proceed to Section 3.")
    else:
        st.info("Waiting for review confirmation.")

# ---------------------------------------------------------------------------
# Section 3 - Push Draw -> Calico
# ---------------------------------------------------------------------------
elif section == "3":
    st.header("Section 3 — Pull Render's Draw → Push to Calico")
    st.caption(SECTION_DESCRIPTIONS["3"])

    if not st.session_state.get("reviewed"):
        st.warning("Please complete Section 2 (Review Pause) first.")
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

# ---------------------------------------------------------------------------
# Section 4 - Pull Results -> Render (mandatory pre-push confirmation)
# ---------------------------------------------------------------------------
elif section == "4":
    st.header("Section 4 — Pull Confirmed Calico Results → Write to Render")
    st.caption(SECTION_DESCRIPTIONS["4"])

    round_seq = st.number_input("Round number", min_value=1, step=1, value=1)

    @st.dialog("Existing results check")
    def results_check_dialog(round_seq_value):
        with st.spinner("Checking existing results on Render..."):
            try:
                existing = sc.count_render_pairings_with_results(round_seq_value)
            except Exception as e:
                st.error(f"Check failed: {e}")
                return

        if existing > 0:
            st.warning(
                f"Render already has {existing} confirmed result(s) recorded for "
                f"Round {round_seq_value}.\n\n"
                f"Pushing again may create duplicate ballots if adjudicators filed "
                f"multiple ballots for the same debate. Continue?"
            )
        else:
            st.info(f"Render has no results recorded yet for Round {round_seq_value}.")

        if st.button("Yes, pull and write results", type="primary"):
            progress = st.empty()

            def cb(done, total):
                progress.write(f"Written {done}/{total} results")

            try:
                team_map = sc.load_team_map()
                if not team_map:
                    st.error("team_map.json not found — run Section 1 first.")
                    return
                team_map = {int(k): v for k, v in team_map.items()}

                render_lookup = sc.build_render_pairing_lookup(round_seq_value)
                written, skipped = sc.write_results_to_render(
                    round_seq_value, team_map, render_lookup, progress_callback=cb
                )
                st.success(f"Wrote {len(written)} result(s) to Render.")
                if skipped:
                    st.info(f"Skipped (no confirmed ballot or no matching Render pairing): {skipped}")
            except Exception as e:
                st.error(f"Failed: {e}")

    if st.button("Pull & Write Results", type="primary"):
        results_check_dialog(int(round_seq))

    st.caption("Clicking this always checks Render's existing result count for this round first.")
