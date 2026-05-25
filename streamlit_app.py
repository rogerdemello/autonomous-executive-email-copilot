from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlparse

import httpx
import streamlit as st
import uvicorn


def _default_dashboard_api_base() -> str:
    app_api_base = os.getenv("APP_API_BASE_URL", "").strip()
    if app_api_base:
        return app_api_base.rstrip("/")

    legacy_api_base = os.getenv("API_BASE_URL", "").strip()
    if legacy_api_base:
        parsed = urlparse(legacy_api_base)
        if parsed.hostname in {"127.0.0.1", "localhost"}:
            return legacy_api_base.rstrip("/")

    return "http://127.0.0.1:8000"


def _looks_like_llm_provider_url(api_base: str) -> bool:
    parsed = urlparse(api_base)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    return "openai.azure.com" in host or "api.openai.com" in host or "/openai/deployments/" in path


def _api_url(api_base: str, path: str) -> str:
    return f"{api_base.rstrip('/')}{path}"


DEFAULT_API_BASE = _default_dashboard_api_base()


def _is_local_api_base(api_base: str) -> bool:
    parsed = urlparse(api_base)
    return parsed.hostname in {"127.0.0.1", "localhost"}


def _health_ok(api_base: str, timeout: float = 1.0) -> bool:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(_api_url(api_base, "/health"))
            return response.status_code == 200
    except Exception:  # noqa: BLE001
        return False


@st.cache_resource(show_spinner=False)
def _start_local_api_server(api_base: str) -> bool:
    if not _is_local_api_base(api_base):
        return False

    if _health_ok(api_base):
        return True

    parsed = urlparse(api_base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000

    config = uvicorn.Config("env.api:app", host=host, port=port, reload=False, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="embedded-fastapi")
    thread.start()

    for _ in range(40):
        if _health_ok(api_base, timeout=0.5):
            return True
        time.sleep(0.25)

    return False


def _get(url: str) -> dict:
    with httpx.Client(timeout=20.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def _post(url: str, payload: dict) -> dict:
    with httpx.Client(timeout=45.0) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


st.set_page_config(page_title="Executive Email Copilot", page_icon="inbox_tray", layout="wide")

st.title("Autonomous Executive Email Copilot")
st.caption(
    "Interactive control center for tasks, grading, baseline runs, and leaderboard benchmarks"
)

with st.sidebar:
    st.header("Connection")
    api_base = st.text_input("API Base URL", value=DEFAULT_API_BASE)
    if _looks_like_llm_provider_url(api_base):
        st.error(
            "This looks like an LLM provider URL, not the app API. "
            "Use APP_API_BASE_URL for the dashboard API (default: http://127.0.0.1:8000)."
        )
    if _is_local_api_base(api_base) and not _health_ok(api_base):
        if _start_local_api_server(api_base):
            st.success(f"Local API auto-started at {api_base}")
        else:
            st.warning("Could not auto-start local API. Verify APP_API_BASE_URL and port settings.")
    check_health = st.button("Check API Health")

if check_health:
    try:
        health = _get(_api_url(api_base, "/health"))
        st.success(f"API connected: {health}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"API health check failed: {exc}")


(
    overview_tab,
    tasks_tab,
    baseline_tab,
    leaderboard_tab,
    grader_tab,
    ai_demo_tab,
    replay_tab,
    approval_tab,
) = st.tabs(
    [
        "Overview",
        "Tasks",
        "Baseline",
        "Leaderboard",
        "Grader",
        "AI Demo",
        "Replay",
        "Approval Queue",
    ]
)

with overview_tab:
    st.subheader("System Status")
    col1, col2 = st.columns(2)
    with col1:
        try:
            health = _get(_api_url(api_base, "/health"))
            st.success("API is online")
            st.json(health)
        except Exception as exc:  # noqa: BLE001
            st.error(f"API unavailable: {exc}")
    with col2:
        st.info("Open API docs at /docs")
        st.code(_api_url(api_base, "/docs"), language="text")

    st.subheader("Quick Start")
    st.markdown(
        """
1. Go to **Tasks** and inspect available task schemas.
2. Run **Baseline** for deterministic benchmark traces.
3. Use **Leaderboard** for multi-seed comparisons.
4. Use **Grader** to score custom action trajectories.
        """
    )

with tasks_tab:
    st.subheader("Task Catalog")
    if st.button("Refresh Tasks"):
        st.rerun()

    try:
        data = _get(_api_url(api_base, "/tasks"))
        st.success("Loaded task metadata")
        st.dataframe(data.get("tasks", []), use_container_width=True)

        with st.expander("Action Schema"):
            st.json(data.get("action_schema", {}))

        with st.expander("Observation Schema"):
            st.json(data.get("observation_schema", {}))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load /tasks: {exc}")

with baseline_tab:
    st.subheader("Baseline Runner")
    c1, c2, c3 = st.columns(3)
    with c1:
        task_id = st.selectbox(
            "Task",
            ["easy_classification", "medium_prioritization", "hard_full_management"],
            index=2,
        )
        persona = st.selectbox("Persona", ["strict_ceo", "balanced", "chill_manager"], index=1)
    with c2:
        mode = st.selectbox("Mode", ["baseline", "stress"], index=0)
        stress_rate = st.slider("Stress Rate", min_value=0.0, max_value=1.0, value=0.5, step=0.05)
    with c3:
        seed = st.number_input("Seed", min_value=0, max_value=999999, value=42, step=1)
        max_steps = st.number_input("Max Steps", min_value=1, max_value=500, value=120, step=1)

    if st.button("Run Baseline"):
        payload = {
            "task_id": task_id,
            "seed": int(seed),
            "persona": persona,
            "mode": mode,
            "stress_rate": float(stress_rate),
            "max_steps": int(max_steps),
        }
        try:
            result = _post(_api_url(api_base, "/baseline"), payload)
            st.success("Baseline run complete")

            m1, m2, m3 = st.columns(3)
            m1.metric("Score", f"{result['score']:.4f}")
            m2.metric("Total Reward", f"{result['total_reward']:.4f}")
            m3.metric("Steps", int(result["steps"]))

            st.subheader("Breakdown")
            st.json(result.get("breakdown", {}))

            st.subheader("Action Trace")
            st.dataframe(result.get("action_trace", []), use_container_width=True)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Baseline request failed: {exc}")

with leaderboard_tab:
    st.subheader("Leaderboard")
    left, right = st.columns(2)
    with left:
        lb_tasks = st.multiselect(
            "Tasks",
            ["easy_classification", "medium_prioritization", "hard_full_management"],
            default=["easy_classification", "medium_prioritization", "hard_full_management"],
        )
        lb_personas = st.multiselect(
            "Personas",
            ["strict_ceo", "balanced", "chill_manager"],
            default=["strict_ceo", "balanced", "chill_manager"],
        )
    with right:
        seed_csv = st.text_input("Seeds (comma separated)", value="42,43,44")
        lb_mode = st.selectbox("Leaderboard Mode", ["baseline", "stress"], index=0)
        lb_stress = st.slider("Leaderboard Stress Rate", 0.0, 1.0, 0.5, 0.05)

    csv_out = st.text_input("Optional CSV Output Path", value="artifacts/streamlit_leaderboard.csv")

    if st.button("Run Leaderboard"):
        try:
            seeds = [int(item.strip()) for item in seed_csv.split(",") if item.strip()]
        except ValueError:
            st.error("Seeds must be comma-separated integers")
            seeds = []

        if seeds and lb_tasks and lb_personas:
            payload = {
                "tasks": lb_tasks,
                "personas": lb_personas,
                "seeds": seeds,
                "max_steps": 120,
                "mode": lb_mode,
                "stress_rate": float(lb_stress),
                "csv_out": csv_out or None,
            }
            try:
                data = _post(_api_url(api_base, "/leaderboard"), payload)
                st.success("Leaderboard generated")

                rows = data.get("rows", [])
                if rows:
                    display_rows = []
                    for row in rows:
                        score_with_ci = f"{row['avg_score']:.3f} ± {row.get('ci_margin_95', 0):.3f}"
                        display_rows.append(
                            {
                                "Task": row["task"],
                                "Persona": row["persona"],
                                "Score (±95% CI)": score_with_ci,
                                "Failure Rate %": row.get("failure_rate_pct", 0),
                                "Fairness Score": row.get("fairness_score", 0),
                                "Avg Reward": row["avg_reward"],
                                "Avg Steps": row["avg_steps"],
                            }
                        )
                    st.dataframe(display_rows, use_container_width=True)

                    st.divider()
                    st.subheader("Score Overview")
                    chart_rows = [
                        {
                            "label": f"{row['task']} | {row['persona']}",
                            "avg_score": row["avg_score"],
                            "ci_lower": row["avg_score"] - row.get("ci_margin_95", 0),
                            "ci_upper": row["avg_score"] + row.get("ci_margin_95", 0),
                        }
                        for row in rows
                    ]
                    st.bar_chart(chart_rows, x="label", y="avg_score")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Leaderboard request failed: {exc}")

with grader_tab:
    st.subheader("Custom Grader")
    g_task = st.selectbox(
        "Task for grading",
        ["easy_classification", "medium_prioritization", "hard_full_management"],
        index=2,
        key="grader_task",
    )
    g_persona = st.selectbox(
        "Persona", ["strict_ceo", "balanced", "chill_manager"], index=1, key="grader_persona"
    )
    g_seed = st.number_input(
        "Seed", min_value=0, max_value=999999, value=42, step=1, key="grader_seed"
    )

    default_actions = [{"action_type": "prioritize", "priority_order": []}]
    action_json = st.text_area(
        "Actions JSON", value=json.dumps(default_actions, indent=2), height=200
    )

    if st.button("Score Trajectory"):
        try:
            parsed_actions = json.loads(action_json)
            if not isinstance(parsed_actions, list):
                raise ValueError("Actions JSON must be a list")

            payload = {
                "task_id": g_task,
                "seed": int(g_seed),
                "persona": g_persona,
                "actions": parsed_actions,
            }
            result = _post(_api_url(api_base, "/grader"), payload)
            st.success("Trajectory scored")
            st.json(result)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Grader request failed: {exc}")

with ai_demo_tab:
    st.subheader("AI Demo - Live LLM Decision Making")
    st.caption("Watch the AI Chief of Staff make decisions in real-time")

    # Demo preset configuration - optimized for baseline-vs-AI comparison
    DEMO_TASK = "hard_full_management"
    DEMO_PERSONA = "balanced"
    DEMO_SEED = 42
    DEMO_MAX_STEPS = 120

    ai_c1, ai_c2, ai_c3 = st.columns(3)
    with ai_c1:
        ai_task_id = st.selectbox(
            "Task",
            ["easy_classification", "medium_prioritization", "hard_full_management"],
            index=2,
            key="ai_task",
        )
        ai_persona = st.selectbox(
            "Persona",
            ["strict_ceo", "balanced", "chill_manager"],
            index=1,
            key="ai_persona",
        )
    with ai_c2:
        ai_mode = st.selectbox(
            "Mode",
            ["compare", "llm", "baseline", "stress"],
            index=0,
            key="ai_mode",
            help="compare: runs both baseline and AI side-by-side",
        )
        ai_stress_rate = st.slider(
            "Stress Rate",
            min_value=0.0,
            max_value=1.0,
            value=0.5,
            step=0.05,
            key="ai_stress",
        )
    with ai_c3:
        ai_seed = st.number_input(
            "Seed",
            min_value=0,
            max_value=999999,
            value=42,
            step=1,
            key="ai_seed",
        )
        ai_max_steps = st.number_input(
            "Max Steps",
            min_value=1,
            max_value=500,
            value=120,
            step=1,
            key="ai_max_steps",
        )

    # Demo preset section
    with st.expander("📽️ Demo Preset (One-Click)", expanded=False):
        st.markdown(f"""
        **Recommended Demo Configuration:**
        - Task: `{DEMO_TASK}` (most complex, shows full AI capability)
        - Persona: `{DEMO_PERSONA}` (balanced reward shaping)
        - Seed: `{DEMO_SEED}` (deterministic, reproducible)
        - Max Steps: `{DEMO_MAX_STEPS}` (complete episode)
        - Mode: `compare` (baseline vs AI side-by-side)

        This tuple is optimized to demonstrate the baseline-vs-AI difference
        with a clear narrative: inbox chaos → baseline → AI → comparison.
        """)
        if st.button("Apply Demo Preset", key="apply_demo_preset"):
            ai_task_id = DEMO_TASK
            ai_persona = DEMO_PERSONA
            ai_seed = DEMO_SEED
            ai_max_steps = DEMO_MAX_STEPS
            ai_mode = "compare"
            st.rerun()

    if st.button("Run AI Demo", key="run_ai_demo"):
        payload = {
            "task_id": ai_task_id,
            "seed": int(ai_seed),
            "persona": ai_persona,
            "mode": ai_mode,
            "stress_rate": float(ai_stress_rate),
            "max_steps": int(ai_max_steps),
        }
        try:
            # For compare mode, run both baseline and llm
            if ai_mode == "compare":
                # Run baseline first
                baseline_payload = payload.copy()
                baseline_payload["mode"] = "baseline"
                baseline_result = _post(_api_url(api_base, "/baseline"), baseline_payload)

                # Run LLM
                llm_payload = payload.copy()
                llm_payload["mode"] = "llm"
                llm_result = _post(_api_url(api_base, "/baseline"), llm_payload)

                # Display comparison
                st.success("Comparison run complete")

                st.subheader("Baseline vs AI Comparison")
                st.caption(f"Same tuple: {ai_task_id} | {ai_persona} | seed={ai_seed}")

                comp_col1, comp_col2, comp_col3 = st.columns(3)
                with comp_col1:
                    st.markdown("### Baseline")
                    st.metric("Score", f"{baseline_result.get('score', 0):.4f}")
                    st.metric("Total Reward", f"{baseline_result.get('total_reward', 0):.4f}")
                    st.metric("Steps", int(baseline_result.get("steps", 0)))
                with comp_col2:
                    st.markdown("### AI (LLM)")
                    st.metric("Score", f"{llm_result.get('score', 0):.4f}")
                    st.metric("Total Reward", f"{llm_result.get('total_reward', 0):.4f}")
                    st.metric("Steps", int(llm_result.get("steps", 0)))
                with comp_col3:
                    st.markdown("### Delta (AI - Baseline)")
                    score_delta = llm_result.get("score", 0) - baseline_result.get("score", 0)
                    reward_delta = llm_result.get("total_reward", 0) - baseline_result.get(
                        "total_reward", 0
                    )
                    steps_delta = llm_result.get("steps", 0) - baseline_result.get("steps", 0)
                    delta_color = "normal" if score_delta >= 0 else "inverse"
                    st.metric("Score Δ", f"{score_delta:+.4f}", delta=delta_color)
                    st.metric("Reward Δ", f"{reward_delta:+.4f}")
                    st.metric("Steps Δ", f"{steps_delta:+d}")

                decision_traces = llm_result.get("decision_traces", [])
                if decision_traces:
                    statuses = [t.get("status", "success") for t in decision_traces]
                    error_count = sum(1 for s in statuses if s in ("error", "unavailable"))
                    fallback_count = sum(1 for s in statuses if s == "fallback")

                    st.divider()
                    if error_count > 0:
                        st.error(f"⚠️ **Degraded Mode:** {error_count} decisions failed")
                    elif fallback_count > 0:
                        st.warning(
                            f"⚠️ **Fallback Mode:** {fallback_count}/{len(decision_traces)} decisions used fallback"
                        )
                    else:
                        st.success("🤖 **Full AI Mode**")

                    st.subheader("AI Decision Timeline")
                    st.caption("Decisions in execution order")

                    timeline_container = st.container()
                    for idx, trace in enumerate(decision_traces):
                        status = trace.get("status", "success")

                        step_num = trace.get("step", idx + 1)
                        action_type = trace.get("action", {}).get("action_type", "unknown")

                        icon = "🤖" if status == "success" else "⚠️"
                        st.markdown(f"**{icon} Step {step_num}: {action_type}**")

                        action_data = trace.get("action", {})
                        if action_data:
                            target_email = action_data.get("email_id", "N/A")
                            st.caption(f"  → Target: {target_email}")

                        reason = trace.get("reason", "No reason provided")
                        st.caption(
                            f"  → Reason: {reason[:200]}{'...' if len(reason) > 200 else ''}"
                        )

                        conf = trace.get("confidence")
                        if conf is not None:
                            st.caption(f"  → Confidence: {conf:.2f}")

                        st.divider()
                else:
                    st.info("No AI decision traces available")

                # Show baseline breakdown
                st.subheader("Baseline Breakdown")
                st.json(baseline_result.get("breakdown", {}))

                # Show AI breakdown
                st.subheader("AI Breakdown")
                st.json(llm_result.get("breakdown", {}))

            else:
                # Original single-mode behavior
                result = _post(_api_url(api_base, "/baseline"), payload)
                st.success("AI Demo run complete")

                m1, m2, m3 = st.columns(3)
                m1.metric("Score", f"{result.get('score', 0):.4f}")
                m2.metric("Total Reward", f"{result.get('total_reward', 0):.4f}")
                m3.metric("Steps", int(result.get("steps", 0)))

                st.subheader("Score Breakdown")
                st.json(result.get("breakdown", {}))

                decision_traces = result.get("decision_traces", [])
                if decision_traces:
                    statuses = [t.get("status", "success") for t in decision_traces]
                    success_count = sum(1 for s in statuses if s == "success")
                    fallback_count = sum(1 for s in statuses if s == "fallback")
                    error_count = sum(1 for s in statuses if s in ("error", "unavailable"))

                    st.subheader("AI Decision Trace")
                    if error_count > 0:
                        st.error(
                            f"⚠️ **Degraded Mode:** {error_count} decision(s) failed (LLM unavailable)"
                        )
                        st.caption(
                            f"Showing {len(decision_traces)} total decisions. Some actions may be fallback responses."
                        )
                    elif fallback_count > 0:
                        st.warning(
                            f"⚠️ **Fallback Mode Active:** {fallback_count}/{len(decision_traces)} decisions used fallback reasoning"
                        )
                        st.caption(
                            "AI ran in fallback mode for some decisions. The story is coherent but may be less optimal."
                        )
                    else:
                        st.success(
                            f"🤖 **Full AI Mode:** {success_count}/{len(decision_traces)} decisions with LLM reasoning"
                        )

                    st.caption("Watch the AI think through each decision")

                    for idx, trace in enumerate(decision_traces):
                        status = trace.get("status", "success")

                        status_icons = {
                            "success": ("✅", "success"),
                            "fallback": ("⚠️", "warning"),
                            "error": ("❌", "error"),
                            "unavailable": ("🚫", "error"),
                        }
                        icon, badge_style = status_icons.get(status, ("❓", "info"))

                        with st.expander(
                            f"{icon} Step {trace.get('step', idx + 1)}: {trace.get('action', {}).get('action_type', 'unknown')}"
                        ):
                            if badge_style == "error":
                                st.error(f"Status: {status.title()}")
                            elif badge_style == "warning":
                                st.warning(f"Status: {status.title()} (fallback used)")
                            else:
                                st.caption(f"Status: {status.title()}")

                            email_ctx = trace.get("email_context", {})
                            st.markdown("**📧 Target Email**")
                            st.markdown(f"- **From:** {email_ctx.get('sender', 'N/A')}")
                            st.markdown(f"- **Subject:** {email_ctx.get('subject', 'N/A')}")
                            st.markdown(f"- **Priority:** {email_ctx.get('priority_hint', 'N/A')}")

                            st.markdown("**🧠 AI Reasoning**")
                            st.markdown(f"{trace.get('reason', 'No reason provided')}")

                            conf = trace.get("confidence")
                            if conf is not None:
                                st.progress(conf, text=f"Confidence: {conf:.2f}")

                            why_not = trace.get("why_not", "")
                            if why_not:
                                st.markdown(f"**❌ Why Not Others:** {why_not}")

                            alts = trace.get("alternatives_considered", [])
                            if alts:
                                st.markdown("**🔄 Alternatives Considered:**")
                                for alt in alts:
                                    st.markdown(f"- {alt}")

                            model_name = trace.get("model_name")
                            latency = trace.get("latency_ms")
                            fallback_reason = trace.get("fallback_reason", "")

                            if model_name or latency:
                                model_info = f"Model: {model_name or 'N/A'}"
                                if latency:
                                    model_info += f" | Latency: {latency:.0f}ms"
                                st.caption(model_info)

                            if fallback_reason:
                                st.caption(f"⬇️ Fallback trigger: {fallback_reason}")

                            st.markdown("**Action Taken:**")
                            st.json(trace.get("action", {}))
                else:
                    st.info("No decision traces available")

        except httpx.ConnectError:
            st.error(
                "Unable to connect to API server. Please ensure the backend is running at the specified URL."
            )
        except httpx.HTTPStatusError as exc:
            st.error(f"API returned an error: {exc.response.status_code} - {exc.response.text}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"AI Demo request failed: {exc}")

with replay_tab:
    st.subheader("Episode Replay Viewer")
    st.caption("View past episodes with decision timeline")

    r_task = st.selectbox(
        "Task",
        ["easy_classification", "medium_prioritization", "hard_full_management"],
        index=2,
        key="replay_task",
    )
    r_persona = st.selectbox(
        "Persona",
        ["strict_ceo", "balanced", "chill_manager"],
        index=1,
        key="replay_persona",
    )
    r_seed = st.number_input(
        "Seed", min_value=0, max_value=999999, value=42, step=1, key="replay_seed"
    )

    episode_id = f"{r_task}_{r_seed}_{r_persona}"

    if st.button("Load Episode"):
        try:
            episode = _get(_api_url(api_base, "/replay") + f"/{episode_id}")
            st.success(f"Episode {episode_id} loaded")

            e1, e2, e3 = st.columns(3)
            with e1:
                st.metric("Score", f"{episode.get('score', 0):.4f}")
            with e2:
                st.metric("Total Reward", f"{episode.get('total_reward', 0):.4f}")
            with e3:
                st.metric("Steps", int(episode.get("steps", 0)))

            st.subheader("Decision Timeline")

            decisions = episode.get("decisions", [])
            if decisions:
                for idx, trace in enumerate(decisions):
                    status = trace.get("status", "success")

                    status_icons = {
                        "success": ("✅", "success"),
                        "fallback": ("⚠️", "warning"),
                        "error": ("❌", "error"),
                        "unavailable": ("🚫", "error"),
                    }
                    icon, badge_style = status_icons.get(status, ("❓", "info"))

                    with st.expander(
                        f"{icon} Step {trace.get('step', idx + 1)}: {trace.get('action', {}).get('action_type', 'unknown')}"
                    ):
                        action_data = trace.get("action", {})
                        if action_data:
                            target_email = action_data.get("email_id", "N/A")
                            st.caption(f"Target: {target_email}")

                        reason = trace.get("reason", "No reason provided")
                        st.markdown(
                            f"**Reason:** {reason[:200]}{'...' if len(reason) > 200 else ''}"
                        )

                        conf = trace.get("confidence")
                        if conf is not None:
                            st.progress(conf, text=f"Confidence: {conf:.2f}")

                        latency = trace.get("latency_ms")
                        model_name = trace.get("model_name")
                        fallback_reason = trace.get("fallback_reason", "")

                        if model_name or latency:
                            model_info = f"Model: {model_name or 'N/A'}"
                            if latency:
                                model_info += f" | Latency: {latency:.0f}ms"
                            st.caption(model_info)

                        if fallback_reason:
                            st.caption(f"Fallback trigger: {fallback_reason}")

                        token_usage = trace.get("token_usage", {})
                        if token_usage:
                            st.caption(f"Tokens: {token_usage.get('total_tokens', 0)}")
            else:
                st.info("No decisions recorded in this episode")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                st.warning(f"Episode {episode_id} not found. Run a baseline first.")
            else:
                st.error(f"API error: {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Failed to load episode: {exc}")

with approval_tab:
    st.subheader("Approval Queue")
    st.caption("Review and approve pending actions")

    if st.button("Refresh Approvals"):
        st.rerun()

    try:
        pending = _get(_api_url(api_base, "/approval/pending"))

        if pending:
            st.write(f"**Pending Approvals ({len(pending)})**")

            for req in pending:
                with st.container():
                    st.markdown(f"**Request ID:** `{req.get('id', 'N/A')}`")
                    st.markdown(
                        f"**Action:** {req.get('action_type', 'N/A')} | **Email:** {req.get('email_id', 'N/A')}"
                    )
                    if req.get("content"):
                        st.markdown(f"**Content:** {req.get('content', '')[:100]}...")
                    if req.get("escalate_to"):
                        st.markdown(f"**Escalate to:** {req.get('escalate_to')}")

                    c1, c2 = st.columns(2)
                    with c1:
                        approve_btn = st.button("Approve", key=f"approve_{req.get('id')}")
                    with c2:
                        reject_btn = st.button("Reject", key=f"reject_{req.get('id')}")

                    if approve_btn:
                        try:
                            _post(
                                _api_url(api_base, f"/approval/{req.get('id')}/approve"),
                                {"approver_id": "admin", "comment": "Approved via UI"},
                            )
                            st.success(f"Approved request {req.get('id')}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to approve: {e}")

                    if reject_btn:
                        try:
                            _post(
                                _api_url(api_base, f"/approval/{req.get('id')}/reject"),
                                {"approver_id": "admin", "comment": "Rejected via UI"},
                            )
                            st.success(f"Rejected request {req.get('id')}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to reject: {e}")

                    st.divider()
        else:
            st.info("No pending approval requests")

        st.subheader("Approval History")
        history_limit = st.slider(
            "History Limit", min_value=5, max_value=100, value=20, key="history_limit"
        )

        try:
            history = _get(_api_url(api_base, f"/approval/history?limit={history_limit}"))
            if history:
                for req in history:
                    status = req.get("status", "unknown")
                    status_icon = (
                        "✅"
                        if status == "approved"
                        else "❌"
                        if status in ("rejected", "expired")
                        else "⏳"
                    )
                    st.markdown(
                        f"{status_icon} **{status.upper()}** - {req.get('action_type')} on {req.get('email_id')} (Requested: {req.get('requested_at', 0):.0f})"
                    )
            else:
                st.info("No approval history")
        except Exception as e:
            st.error(f"Failed to load history: {e}")

    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load approvals: {exc}")
