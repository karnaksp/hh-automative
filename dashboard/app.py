"""Streamlit dashboard for hh-automative DuckDB logs."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from hh_automative.analytics import live_db_path_for
from hh_automative.dashboard_data import (
    DashboardAlert,
    build_ai_status_table,
    build_alerts,
    build_latest_cover_letters,
    build_live_activity,
    build_manual_review_vacancies,
    build_overview_metrics,
    build_problem_vacancies,
    build_profile_matrix,
    build_recent_ai_table,
    build_run_summary,
    build_status_timeline,
    build_top_error_table,
    discover_duckdb_paths,
    extract_cover_letter_text,
    filter_by_timerange,
    filter_dashboard_events,
    prepare_events_frame,
    safe_json_loads,
    vacancy_results,
    with_review_columns,
)
from hh_automative.errors import ConfigError
from hh_automative.run_launcher import (
    PipelineLaunchRequest,
    available_resume_names,
    launch_pipeline,
)

DEFAULT_DB_PATH = Path(os.getenv("HH_DASHBOARD_DB_PATH", "data/hh_automative.duckdb"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]


st.set_page_config(
    page_title="hh-automative monitor",
    page_icon="📊",
    layout="wide",
)


def main() -> None:
    _inject_styles()
    st.title("hh-automative monitor")
    st.caption("Локальная панель по запускам, сбоям, LLM и качеству автоматизации hh.ru")

    sidebar = render_sidebar()
    render_pipeline_launcher(sidebar.db_path)

    @st.fragment(run_every=f"{sidebar.refresh_seconds}s" if sidebar.auto_refresh else None)
    def render_dashboard() -> None:
        db_path = sidebar.db_path
        if not db_path.exists():
            st.warning(f"DuckDB file not found: {db_path}")
            return

        events = prepare_events_frame(load_table(db_path, "automation_events"), "event_ts")
        logs = prepare_events_frame(load_table(db_path, "app_logs"), "log_ts")
        ai_events = prepare_events_frame(load_table(db_path, "ai_assist_events"), "event_ts")

        if events.empty and logs.empty and ai_events.empty:
            st.info("Пока нет логов. Запусти run, dry-run или LLM-команду.")
            return

        events = filter_by_timerange(events, "event_ts", sidebar.time_window)
        logs = filter_by_timerange(logs, "log_ts", sidebar.time_window)
        ai_events = filter_by_timerange(ai_events, "event_ts", sidebar.time_window)

        filtered_events = filter_dashboard_events(
            events,
            profiles=sidebar.selected_profiles,
            statuses=sidebar.selected_statuses,
            search_text=sidebar.search_text,
        )
        filtered_ai_events = _filter_ai_events(
            ai_events, sidebar.selected_profiles, sidebar.search_text
        )
        vacancy_events = with_review_columns(vacancy_results(filtered_events))

        metrics = build_overview_metrics(filtered_events, vacancy_events, filtered_ai_events, logs)
        alerts = build_alerts(vacancy_events, filtered_ai_events, logs)

        if sidebar.auto_refresh:
            st.caption(f"Live refresh every {sidebar.refresh_seconds}s")
            components.html("<!-- live refresh enabled -->", height=0)

        render_health_banner(db_path, metrics, vacancy_events, filtered_ai_events)
        render_alerts(alerts)
        render_kpis(metrics)
        render_live_feed(vacancy_events, filtered_ai_events, logs, sidebar.live_rows)
        render_operational_overview(vacancy_events, filtered_events, filtered_ai_events)
        render_problem_analysis(vacancy_events, logs)
        render_ai_section(filtered_ai_events)
        render_detail_tabs(filtered_events, vacancy_events, logs, filtered_ai_events)

    render_dashboard()


def render_pipeline_launcher(db_path: Path) -> None:
    with st.expander("Launch pipeline", expanded=False):
        render_recent_launched_pipelines(db_path)
        try:
            resumes = available_resume_names()
        except ConfigError as exc:
            st.warning(str(exc))
            return
        if not resumes:
            st.warning("Нет резюме в config/resumes.json. Сначала синхронизируй резюме.")
            return

        with st.form("pipeline_launcher_form"):
            top_left, top_right = st.columns((1.1, 0.9))
            with top_left:
                label = st.text_input("Run label", value="custom")
                query = st.text_input(
                    "Search query",
                    value="data engineer",
                    placeholder="teacher, уборщик, dwh developer",
                )
                advanced_search_url = st.text_input(
                    "Advanced hh.ru search URL",
                    value="",
                    placeholder="Optional: paste full hh.ru search URL",
                )
                exclude = st.text_area(
                    "Exclude words",
                    value="",
                    height=90,
                    placeholder="sales, recruiter, data scientist",
                )
            with top_right:
                resume_name = st.selectbox("Resume", resumes)
                limit = st.number_input("New attempts limit", min_value=1, max_value=100, value=10)
                dry_run = st.toggle("Dry run", value=True)
                target_new = st.toggle("Skip history until limit", value=True)
                ignore_dry_run_history = st.toggle("Ignore dry-run history", value=True)
                headless = st.toggle("Headless browser", value=True)
                only_with_salary = st.toggle("Only with salary", value=False)
                min_salary = st.text_input("Min salary", value="", placeholder="Optional")

            submitted = st.form_submit_button("Start pipeline", type="primary", width="stretch")

        if not submitted:
            return
        try:
            result = launch_pipeline(
                PipelineLaunchRequest(
                    query=query.strip(),
                    exclude=exclude.strip(),
                    resume_name=str(resume_name),
                    limit=int(limit),
                    dry_run=bool(dry_run),
                    target_new=bool(target_new),
                    ignore_dry_run_history=bool(ignore_dry_run_history),
                    headless=bool(headless),
                    min_salary=min_salary.strip(),
                    only_with_salary=bool(only_with_salary),
                    advanced_search_url=advanced_search_url.strip(),
                    label=label.strip(),
                ),
                cwd=PROJECT_ROOT,
            )
        except ConfigError as exc:
            st.error(str(exc))
            return
        st.success(f"Started `{result.profile_name}` as PID {result.pid}.")
        st.code(" ".join(result.command), language="bash")
        runtime_files = [str(result.search_profiles_path), str(result.resumes_path)]
        log_path = getattr(result, "log_path", None)
        if log_path:
            runtime_files.append(str(log_path))
        st.caption(
            "Runtime files: " + ", ".join(f"`{path}`" for path in runtime_files)
        )


def render_recent_launched_pipelines(db_path: Path) -> None:
    events = prepare_events_frame(load_table(db_path, "automation_events"), "event_ts")
    if events.empty or "profile" not in events.columns:
        return
    adhoc = events[events["profile"].astype(str).str.startswith("adhoc-")].copy()
    if adhoc.empty:
        return
    latest = (
        adhoc.sort_values("event_ts")
        .groupby("run_id", dropna=False)
        .tail(1)
        .sort_values("event_ts", ascending=False)
        .head(10)
        .copy()
    )
    latest["run_state"] = latest.apply(_adhoc_run_state, axis=1)
    st.caption("Recent dashboard launches")
    st.dataframe(
        latest[["event_ts", "run_state", "profile", "event_type", "status"]],
        width="stretch",
        hide_index=True,
    )


def _adhoc_run_state(row: pd.Series) -> str:
    if str(row.get("event_type", "")) == "run_finished":
        return "finished"
    return "running or interrupted"


class SidebarState:
    def __init__(
        self,
        db_path: Path,
        time_window: int | None,
        selected_profiles: list[str],
        selected_statuses: list[str],
        search_text: str,
        auto_refresh: bool,
        refresh_seconds: int,
        live_rows: int,
    ) -> None:
        self.db_path = db_path
        self.time_window = time_window
        self.selected_profiles = selected_profiles
        self.selected_statuses = selected_statuses
        self.search_text = search_text
        self.auto_refresh = auto_refresh
        self.refresh_seconds = refresh_seconds
        self.live_rows = live_rows


def render_sidebar() -> SidebarState:
    discovered = discover_duckdb_paths(DEFAULT_DB_PATH.parent)
    labels = {path.name: path for path in discovered}
    default_name = DEFAULT_DB_PATH.name if DEFAULT_DB_PATH.name in labels else (discovered[0].name if discovered else DEFAULT_DB_PATH.name)

    with st.sidebar:
        st.subheader("Source")
        selected_name = st.selectbox("DuckDB file", list(labels) or [default_name], index=(list(labels).index(default_name) if labels else 0))
        path_value = labels.get(selected_name, DEFAULT_DB_PATH)
        custom_path = st.text_input("Custom path", str(path_value))
        if st.button("Refresh", width="stretch"):
            st.cache_data.clear()

        st.subheader("Filters")
        time_window_label = st.selectbox("Time window", ["All time", "Last 24h", "Last 7d", "Last 30d"], index=2)
        time_window = {
            "All time": None,
            "Last 24h": 1,
            "Last 7d": 7,
            "Last 30d": 30,
        }[time_window_label]

        st.subheader("Live")
        auto_refresh = st.checkbox("Auto refresh", value=True)
        refresh_seconds = st.select_slider("Refresh interval (sec)", options=[2, 5, 10, 15, 30], value=5)
        live_rows = st.select_slider("Live rows", options=[20, 50, 100, 200], value=50)

        preview_events = prepare_events_frame(load_table(Path(custom_path), "automation_events"), "event_ts")
        profiles = sorted(preview_events["profile"].dropna().unique().tolist()) if not preview_events.empty else []
        statuses = sorted(preview_events["status"].dropna().unique().tolist()) if not preview_events.empty else []
        selected_profiles = st.multiselect("Profile", profiles, default=profiles)
        selected_statuses = st.multiselect("Result status", statuses, default=statuses)
        search_text = st.text_input("Search", placeholder="title, url, company, error")

    return SidebarState(
        db_path=Path(custom_path),
        time_window=time_window,
        selected_profiles=selected_profiles,
        selected_statuses=selected_statuses,
        search_text=search_text,
        auto_refresh=auto_refresh,
        refresh_seconds=refresh_seconds,
        live_rows=live_rows,
    )


@st.cache_data(ttl=5)
def load_table(db_path: Path, table: str) -> pd.DataFrame:
    if not db_path.exists():
        live_path = live_db_path_for(db_path)
        if live_path.exists():
            return _load_table_from_sqlite(live_path, table)
        return pd.DataFrame()
    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except duckdb.Error:
        live_path = live_db_path_for(db_path)
        if live_path.exists():
            return _load_table_from_sqlite(live_path, table)
        return pd.DataFrame()
    try:
        exists = con.execute(
            """
            select count(*)
            from information_schema.tables
            where table_name = ?
            """,
            [table],
        ).fetchone()[0]
        if not exists:
            live_path = live_db_path_for(db_path)
            if live_path.exists():
                return _load_table_from_sqlite(live_path, table)
            return pd.DataFrame()
        return con.sql(f"select * from {table}").df()
    finally:
        con.close()


def _load_table_from_sqlite(db_path: Path, table: str) -> pd.DataFrame:
    import sqlite3

    if not db_path.exists():
        return pd.DataFrame()
    try:
        con = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return pd.DataFrame()
    try:
        exists = con.execute(
            "select count(*) from sqlite_master where type='table' and name=?",
            [table],
        ).fetchone()[0]
        if not exists:
            return pd.DataFrame()
        return pd.read_sql_query(f"select * from {table}", con)
    finally:
        con.close()


def render_health_banner(
    db_path: Path,
    metrics: dict[str, object],
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame,
) -> None:
    latest_event = vacancy_events["event_ts"].max() if not vacancy_events.empty else None
    db_label = f"`{db_path.name}`"
    freshness = latest_event.strftime("%Y-%m-%d %H:%M") if pd.notna(latest_event) else "no vacancy events"
    llm_label = _health_label(metrics["ai_answered"], metrics["ai_failed"])
    with st.container(border=True):
        st.markdown(
            f"""
            <div class="hero-block">
              <div>
                <div class="hero-eyebrow">Current dataset</div>
                <div class="hero-title">{db_label}</div>
                <div class="hero-meta">Latest vacancy event: {freshness}</div>
              </div>
              <div class="hero-stats">
                <div><span class="hero-stat-label">Latest run</span><br><strong>{metrics['latest_run_vacancies']}</strong> vacancies</div>
                <div><span class="hero-stat-label">Run success rate</span><br><strong>{metrics['latest_run_success_rate']:.0f}%</strong></div>
                <div><span class="hero-stat-label">LLM path</span><br><strong>{llm_label}</strong></div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_alerts(alerts: list[DashboardAlert]) -> None:
    st.subheader("Key insights")
    if not alerts:
        st.success("В текущем срезе явных проблем не найдено.")
        return
    for alert in alerts:
        st.markdown(
            f"""
            <div class="insight insight-{alert.severity}">
              <div class="insight-title">{alert.title}</div>
              <div class="insight-detail">{alert.detail}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_kpis(metrics: dict[str, object]) -> None:
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Runs", int(metrics["runs"]))
    col2.metric("Vacancies", int(metrics["vacancies"]))
    col3.metric("Success rate", f"{metrics['success_rate']:.0f}%")
    col4.metric("Failures", int(metrics["failures"]))
    col5.metric("LLM answered", int(metrics["ai_answered"]))
    col6.metric("Warnings / errors", int(metrics["warnings"]) + int(metrics["errors"]))


def render_operational_overview(
    vacancy_events: pd.DataFrame,
    events: pd.DataFrame,
    ai_events: pd.DataFrame,
) -> None:
    left, right = st.columns((1.3, 1))

    with left:
        st.subheader("Run health")
        timeline = build_status_timeline(vacancy_events)
        if timeline.empty:
            st.info("Недостаточно vacancy_result для тренда.")
        else:
            timeline_table = timeline.reset_index(names="day").sort_values("day", ascending=False)
            st.dataframe(
                timeline_table,
                width="stretch",
                hide_index=True,
            )
        runs = build_run_summary(events)
        if runs.empty:
            st.info("Нет run summary.")
        else:
            st.dataframe(
                runs[
                    [
                        "started_at",
                        "profile",
                        "vacancies",
                        "success",
                        "failure",
                        "skipped",
                        "dry_run",
                        "success_rate",
                        "duration_minutes",
                    ]
                ],
                width="stretch",
                hide_index=True,
                column_config={"success_rate": st.column_config.NumberColumn(format="%.0f%%")},
            )

    with right:
        st.subheader("Profile mix")
        profile_matrix = build_profile_matrix(vacancy_events)
        if profile_matrix.empty:
            st.info("Нет профилей в текущем срезе.")
        else:
            st.dataframe(profile_matrix, width="stretch", hide_index=True)

        st.subheader("AI status")
        ai_status = build_ai_status_table(ai_events)
        if ai_status.empty:
            st.info("Нет AI assist событий.")
        else:
            st.dataframe(ai_status, width="stretch", hide_index=True)


def render_live_feed(
    vacancy_events: pd.DataFrame,
    ai_events: pd.DataFrame,
    logs: pd.DataFrame,
    live_rows: int,
) -> None:
    st.subheader("Live feed")
    live_feed = build_live_activity(vacancy_events, ai_events, logs, limit=live_rows)
    if live_feed.empty:
        st.info("Нет live-событий в текущем срезе.")
        return
    st.dataframe(
        live_feed,
        width="stretch",
        hide_index=True,
        column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
    )


def render_problem_analysis(vacancy_events: pd.DataFrame, logs: pd.DataFrame) -> None:
    left, right = st.columns((1.1, 1))

    with left:
        st.subheader("Top blockers")
        blockers = build_top_error_table(vacancy_events, limit=10)
        if blockers.empty:
            st.success("Повторяющихся blockers не найдено.")
        else:
            st.dataframe(
                blockers,
                width="stretch",
                hide_index=True,
                column_config={"example_url": st.column_config.LinkColumn("example_url")},
            )

        st.subheader("Problem vacancies")
        problems = build_problem_vacancies(vacancy_events, limit=20)
        if problems.empty:
            st.success("В текущем срезе нет problem vacancies.")
        else:
            st.dataframe(
                problems,
                width="stretch",
                hide_index=True,
                column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
            )

    with right:
        st.subheader("Log hotspots")
        if logs.empty:
            st.info("Нет app_logs в выбранной базе.")
        else:
            log_summary = (
                logs.groupby(["level", "logger"])
                .size()
                .reset_index(name="count")
                .sort_values(["count", "level"], ascending=[False, True])
                .head(15)
            )
            st.dataframe(log_summary, width="stretch", hide_index=True)
            recent_log_rows = logs[logs["level"].isin(["WARNING", "ERROR"])].sort_values(
                "log_ts", ascending=False
            )
            if recent_log_rows.empty:
                st.success("Нет warning/error логов.")
            else:
                st.dataframe(
                    recent_log_rows[["log_ts", "level", "logger", "message"]].head(20),
                    width="stretch",
                    hide_index=True,
                )


def render_ai_section(ai_events: pd.DataFrame) -> None:
    st.subheader("AI assist")
    left, right = st.columns((1, 1.2))

    with left:
        recent_ai = build_recent_ai_table(ai_events, limit=15)
        if recent_ai.empty:
            st.info("Нет AI assist событий в текущем срезе.")
        else:
            st.dataframe(
                recent_ai,
                width="stretch",
                hide_index=True,
                column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
            )

    with right:
        latest_cover_letters = build_latest_cover_letters(ai_events, limit=5)
        if latest_cover_letters.empty:
            st.info("Нет answered cover_letter событий.")
        else:
            selected_index = st.selectbox(
                "Latest cover letters",
                options=list(range(len(latest_cover_letters))),
                format_func=lambda index: _cover_letter_option_label(latest_cover_letters.iloc[index]),
            )
            selected = latest_cover_letters.iloc[int(selected_index)]
            metadata = safe_json_loads(str(selected["metadata_json"]))
            st.caption(str(selected["vacancy_url"]))
            st.text_area(
                "Cover letter text",
                value=str(selected["cover_letter"]),
                height=220,
                disabled=True,
                key="cover_letter_text_preview_main",
            )
            with st.expander("Generation context"):
                st.text_area(
                    "Vacancy text",
                    value=str(metadata.get("vacancy_text", "")),
                    height=220,
                    disabled=True,
                    key="cover_letter_vacancy_context_main",
                )
                st.text_area(
                    "Resume text",
                    value=str(metadata.get("resume_text", "")),
                    height=180,
                    disabled=True,
                    key="cover_letter_resume_context_main",
                )


def render_detail_tabs(
    events: pd.DataFrame,
    vacancy_events: pd.DataFrame,
    logs: pd.DataFrame,
    ai_events: pd.DataFrame,
) -> None:
    st.subheader("Detailed views")
    tab_manual, tab_vacancies, tab_runs, tab_ai, tab_logs, tab_raw = st.tabs(
        ["Manual Review", "Vacancies", "Runs", "AI details", "Logs", "Raw"]
    )

    with tab_manual:
        review = build_manual_review_vacancies(vacancy_events, ai_events, limit=200)
        if review.empty:
            st.success("Нет вакансий, требующих ручного разбора.")
        else:
            st.dataframe(
                review,
                width="stretch",
                hide_index=True,
                column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
            )

    with tab_vacancies:
        if vacancy_events.empty:
            st.info("Нет vacancy_result событий.")
        else:
            st.dataframe(
                vacancy_events[
                    [
                        "event_ts",
                        "status",
                        "profile",
                        "title",
                        "company",
                        "selected_resume",
                        "vacancy_url",
                        "message",
                        "error_reason",
                        "error_category",
                        "severity",
                        "recommended_action",
                        "diagnostics_path",
                    ]
                ],
                width="stretch",
                hide_index=True,
                column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
            )

    with tab_runs:
        runs = build_run_summary(events)
        if runs.empty:
            st.info("Нет run summary.")
        else:
            st.dataframe(runs, width="stretch", hide_index=True)

    with tab_ai:
        _render_ai_details(ai_events)

    with tab_logs:
        if logs.empty:
            st.info("Нет логов.")
        else:
            st.dataframe(
                logs.sort_values("log_ts", ascending=False),
                width="stretch",
                hide_index=True,
            )

    with tab_raw:
        with st.expander("automation_events", expanded=False):
            st.dataframe(events.sort_values("event_ts", ascending=False), width="stretch")
        with st.expander("ai_assist_events", expanded=False):
            st.dataframe(ai_events.sort_values("event_ts", ascending=False), width="stretch")
        with st.expander("app_logs", expanded=False):
            st.dataframe(logs.sort_values("log_ts", ascending=False), width="stretch")


def _render_ai_details(ai_events: pd.DataFrame) -> None:
    if ai_events.empty:
        st.info("Нет AI assist details.")
        return

    answered = ai_events[ai_events["status"] == "answered"].copy()
    failed = ai_events[ai_events["status"] == "failed"].copy()
    cover_letter_events = answered[answered["task_type"] == "cover_letter"].copy()

    top, bottom = st.columns((1.1, 0.9))
    with top:
        if cover_letter_events.empty:
            st.info("Нет answered cover_letter events.")
        else:
            latest = cover_letter_events.sort_values("event_ts", ascending=False).iloc[0]
            metadata = safe_json_loads(str(latest["metadata_json"]))
            st.caption(f"{latest['event_ts']} | {latest['profile']} | {latest['vacancy_url']}")
            st.text_area(
                "Latest cover letter",
                value=extract_cover_letter_text(str(latest["parsed_json"])),
                height=220,
                disabled=True,
                key="latest_cover_letter_details",
            )
            with st.expander("Prompt and raw response"):
                st.code(str(latest["prompt"]), language="text")
                st.code(str(latest["response_text"]), language="json")
            with st.expander("Stored context"):
                st.text_area(
                    "Vacancy text",
                    value=str(metadata.get("vacancy_text", "")),
                    height=220,
                    disabled=True,
                    key="latest_cover_letter_vacancy_text",
                )
                st.text_area(
                    "Resume text",
                    value=str(metadata.get("resume_text", "")),
                    height=180,
                    disabled=True,
                    key="latest_cover_letter_resume_text",
                )

    with bottom:
        if failed.empty:
            st.success("Нет failed AI events.")
        else:
            st.dataframe(
                failed[["event_ts", "task_type", "vacancy_url", "error_reason"]].sort_values(
                    "event_ts", ascending=False
                ),
                width="stretch",
                hide_index=True,
                column_config={"vacancy_url": st.column_config.LinkColumn("vacancy_url")},
            )


def _filter_ai_events(
    ai_events: pd.DataFrame,
    selected_profiles: list[str],
    search_text: str,
) -> pd.DataFrame:
    if ai_events.empty:
        return ai_events.copy()
    filtered = ai_events.copy()
    if selected_profiles:
        filtered = filtered[filtered["profile"].isin(selected_profiles)]
    normalized_search = search_text.strip().casefold()
    if normalized_search:
        haystack = (
            filtered["vacancy_url"].astype(str)
            + " "
            + filtered["error_reason"].astype(str)
            + " "
            + filtered["response_text"].astype(str)
        ).str.casefold()
        filtered = filtered[haystack.str.contains(normalized_search, na=False)]
    return filtered


def _cover_letter_option_label(row: pd.Series) -> str:
    metadata = safe_json_loads(str(row.get("metadata_json", "")))
    vacancy_text = str(metadata.get("vacancy_text", ""))
    prefix = vacancy_text[:60].replace("\n", " ").strip() if vacancy_text else row.get("vacancy_url", "")
    return f"{row['event_ts']} | {prefix}"


def _health_label(answered_count: object, failed_count: object) -> str:
    answered = int(answered_count)
    failed = int(failed_count)
    if answered > 0 and failed == 0:
        return "answered"
    if answered > 0 and failed > 0:
        return "mixed"
    if failed > 0:
        return "failing"
    return "idle"


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        .hero-block {
            display: flex;
            justify-content: space-between;
            gap: 24px;
            align-items: flex-start;
        }
        .hero-eyebrow {
            color: #6b7280;
            font-size: 0.85rem;
            margin-bottom: 0.35rem;
        }
        .hero-title {
            font-size: 1.4rem;
            font-weight: 700;
            margin-bottom: 0.35rem;
        }
        .hero-meta {
            color: #4b5563;
            font-size: 0.92rem;
        }
        .hero-stats {
            display: grid;
            grid-template-columns: repeat(3, minmax(110px, 1fr));
            gap: 16px;
            width: min(520px, 100%);
        }
        .hero-stat-label {
            color: #6b7280;
            font-size: 0.8rem;
        }
        .insight {
            border: 1px solid #e5e7eb;
            border-left-width: 5px;
            border-radius: 8px;
            padding: 12px 14px;
            margin-bottom: 10px;
            background: #ffffff;
        }
        .insight-title {
            font-weight: 600;
            margin-bottom: 4px;
        }
        .insight-detail {
            color: #374151;
            font-size: 0.94rem;
        }
        .insight-critical { border-left-color: #dc2626; background: #fef2f2; }
        .insight-warning { border-left-color: #d97706; background: #fff7ed; }
        .insight-info { border-left-color: #2563eb; background: #eff6ff; }
        .insight-success { border-left-color: #059669; background: #ecfdf5; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
