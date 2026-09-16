import streamlit as st
import json
import sync_core as sc

st.set_page_config(page_title="Render <-> Calico Sync", layout="centered")

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
    "1.5": "Before generating a draw: sync which teams checked in on Calico so Render's draw generation uses the right pool.",
    "2": "Review the generated draw on Render's UI, then push it to Calico.",
    "3": "After a round is played on Calico, pull confirmed results into Render.",
    "4": "Compare final standings between Calico and Render to confirm they match.",
}

section = st.sidebar.radio(
    "Section",
    ["0", "1", "1.5", "2", "3", "4"],
    format_func=lambda s: {
        "0": "0. Overview",
        "1": "1. Import Teams",
        "1.5": "1.5 Sync Team Availability",
        "2": "2. Push Draw → Calico",
        "3": "3. Pull Results → Render",
        "4": "4. Compare Standings",
    }[s],
)

if section == "0":
    st.header("Section 0 — Overview")
    st.caption(SECTION_DESCRIPTIONS["0"])

    try:
        render_name = sc.get_render_tournament_name()
    except Exception:
        render_name = f"(couldn't fetch name — `{sc.RENDER_URL}`)"

    try:
        calico_name = sc.get_calico_tournament_name()
    except Exception:
        calico_name = f"(couldn't fetch name — `{sc.CALICO_URL}`)"

    st.markdown(
        f"**Render tournament:** {render_name}\n\n"
        f"**Calico tournament:** {calico_name}"
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
    st.subheader("team_map.json backup / restore")
    st.caption(
        "Render's filesystem is not guaranteed to persist across deploys/restarts. "
        "After Section 1 finishes, download team_map.json and keep it somewhere safe. "
        "If it's ever missing (Sections 2 and 3 will error saying so), re-upload it here."
    )
    existing_map = sc.load_team_map()
    if existing_map:
        st.success(f"team_map.json currently present on this instance ({len(existing_map)} teams mapped).")
        st.download_button(
            "Download current team_map.json",
            data=json.dumps(existing_map, indent=2),
            file_name="team_map.json",
            mime="application/json",
        )
    else:
        st.warning("No team_map.json currently found on this instance.")

    uploaded = st.file_uploader("Restore team_map.json", type="json")
    if uploaded is not None:
        try:
            restored = json.loads(uploaded.read())
            restored = {int(k): v for k, v in restored.items()}
            sc.save_team_map(restored)
            st.success(f"Restored team_map.json with {len(restored)} team mapping(s).")
        except Exception as e:
            st.error(f"Failed to restore: {e}")
    st.divider()
    st.subheader("Add dummy adjudicators & rooms")
    st.caption(
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

elif section == "1":
    st.header("Section 1 — One-time Team Import")
    st.caption(SECTION_DESCRIPTIONS["1"])
    st.warning("Run this once only, before Round 1. Re-running will create duplicate teams.")

    @st.dialog("Team count check")
    def import_check_dialog():
        with st.spinner("Checking team counts on Calico and Render..."):
            try:
                x, y, status, info = sc.check_team_import_readiness()
            except Exception as e:
                st.error(f"Check failed: {e}")
                return

        st.markdown(f"**Calico's Teams = {x}**")
        st.markdown(f"**Render's Teams = {y}**")

        if status == "ready":
            st.success("Teams are ready to copy!")
            if st.button("Proceed with Import", type="primary"):
                progress = st.progress(0.0)
                status_line = st.empty()

                def cb(done, total, name):
                    progress.progress(done / total)
                    status_line.write(f"Created team {done}/{total}: {name}")

                try:
                    team_map = sc.import_teams(progress_callback=cb)
                    st.success(f"Imported {len(team_map)} teams. team_map.json written.")
                    st.download_button(
                        "Download team_map.json (keep this safe!)",
                        data=json.dumps(team_map, indent=2),
                        file_name="team_map.json",
                        mime="application/json",
                    )
                except Exception as e:
                    st.error(f"Import failed: {e}")

        elif status == "empty_source":
            st.warning(info)

        elif status == "duplicate_risk":
            st.error(info)
            st.caption("All Calico teams already exist on Render by reference — nothing to import.")

        elif status == "partial":
            missing = info  # list of Calico team dicts still missing on Render
            st.warning(
                f"{len(missing)} of {x} teams are missing on Render: "
                + ", ".join(t["reference"] for t in missing[:10])
                + (", ..." if len(missing) > 10 else "")
            )
            if st.button("Fill remaining teams", type="primary"):
                progress = st.progress(0.0)
                status_line = st.empty()

                def cb(done, total, name):
                    progress.progress(done / total)
                    status_line.write(f"Created team {done}/{total}: {name}")

                try:
                    missing_ids = [t["id"] for t in missing]
                    team_map = sc.import_teams(progress_callback=cb, only_calico_ids=missing_ids)
                    st.success(f"Created {len(missing_ids)} missing team(s). team_map.json updated.")
                    st.download_button(
                        "Download team_map.json (keep this safe!)",
                        data=json.dumps(team_map, indent=2),
                        file_name="team_map.json",
                        mime="application/json",
                    )
                except Exception as e:
                    st.error(f"Import failed: {e}")

    if st.button("Run Team Import", type="primary"):
        import_check_dialog()

    st.caption("Clicking this always re-checks live counts first — go back to Section 0 "
               "any time to double check the numbers match your expectations.")

elif section == "1.5":
    st.header("Section 1.5 — Sync Team Availability (Calico → Render)")
    st.caption(SECTION_DESCRIPTIONS["1.5"])
    st.markdown(
        "Teams check in on **Calico** (the public site) via their private URLs. "
        "Run this **before generating each round's draw on Render**, so Render's "
        "draw generation only considers teams that actually checked in."
    )
    st.warning(
        "⚠️ Before running this, manually clear all team availability on **Render's own UI** "
        "for this round. Tabbycat's API 500s if any team in the new list is already marked "
        "available — clearing first avoids that."
    )

    round_seq = st.number_input("Round number", min_value=1, step=1, value=1, key="avail_round")

    @st.dialog("Availability check")
    def availability_check_dialog(round_seq_value):
        with st.spinner("Fetching checked-in teams from Calico..."):
            try:
                team_map = sc.load_team_map()
                if not team_map:
                    st.error("team_map.json not found — run Section 1 first.")
                    return
                team_map = {int(k): v for k, v in team_map.items()}
                result = sc.check_availability_sync(round_seq_value, team_map)
            except Exception as e:
                st.error(f"Check failed: {e}")
                return

        st.markdown(f"**{result['total_available']} team(s) currently marked available on Calico for Round {round_seq_value}.**")

        n_resolved = len(result["resolved_render_urls"])
        n_unresolved = len(result["unresolved_ids"])

        if n_resolved:
            st.success(
                f"✅ {n_resolved} team(s) resolved and will be marked available on Render: "
                + ", ".join(result["resolved_refs"][:15])
                + (", ..." if n_resolved > 15 else "")
            )
        else:
            st.warning("No teams could be resolved — nothing would be written to Render.")

        if n_unresolved:
            st.warning(
                f"⚠️ {n_unresolved} team(s) are not in team_map.json and will be skipped: "
                + ", ".join(result["unresolved_refs"][:15])
                + (", ..." if n_unresolved > 15 else "")
                + "\n\nRe-run **Section 1 → Fill remaining teams** to map these, then retry this section."
            )

        if result["total_available"] and result["total_available"] < 0.5 * max(sc.get_calico_team_count(), 1):
            st.info(
                "Fewer than half of all Calico teams are checked in — double check that "
                "check-ins are actually complete before pushing."
            )

        if n_resolved and st.button("Push availability to Render", type="primary"):
            try:
                sc.push_team_availability_to_render(round_seq_value, result["resolved_render_urls"])
                st.success(f"Marked {n_resolved} team(s) available on Render for Round {round_seq_value}.")
                if n_unresolved:
                    st.warning(f"{n_unresolved} team(s) were skipped — see above.")
            except Exception as e:
                st.error(f"Failed to push availability: {e}")

    if st.button("Check & Sync Availability", type="primary"):
        availability_check_dialog(int(round_seq))

    st.caption("Clicking this always re-fetches live check-in status from Calico first — "
               "nothing is written until you confirm on the resolved/unresolved breakdown.")

elif section == "2":
    st.header("Section 2 — Review Draw → Push to Calico")
    st.caption(SECTION_DESCRIPTIONS["2"])
    st.markdown(
        "Go to **Render's Tabbycat UI**, generate the draw for the next round, "
        "and review it there (adjudicator allocation, venues, etc. are **not synced** "
        "by this tool — assign them on Render or directly on Calico afterward).\n\n"
        "Once you're happy with it, tick the box below to unlock pushing it to Calico."
    )
    reviewed = st.checkbox("I've generated and reviewed the draw on Render's UI ✅")

    if not reviewed:
        st.info("Waiting for review confirmation.")
        st.stop()

    st.success("Review confirmed.")
    st.divider()

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

elif section == "3":
    st.header("Section 3 — Pull Confirmed Calico Results → Write to Render")
    st.caption(SECTION_DESCRIPTIONS["3"])

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

elif section == "4":
    st.header("Section 4 — Compare Standings")
    st.caption(SECTION_DESCRIPTIONS["4"])

    mismatches_first = st.checkbox("Show mismatches first")

    if st.button("Compare Standings", type="primary"):
        with st.spinner("Fetching standings from both instances..."):
            try:
                rows, all_match = sc.compare_standings()
            except Exception as e:
                st.error(f"Failed: {e}")
                st.stop()

        if all_match:
            st.success("Yes, all teams match!")
        else:
            mismatch_count = sum(1 for r in rows if r["status"] != "Match")
            st.error(f"Teams don't match — {mismatch_count} issue(s) found.")

        if mismatches_first:
            rows = sorted(rows, key=lambda r: (r["status"] == "Match", r["calico_rank"] or 999))
        else:
            rows = sorted(rows, key=lambda r: r["calico_rank"] or 999)

        st.dataframe(rows, use_container_width=True)
