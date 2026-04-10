"""Report analysis tools — multi-build aggregation for the comprehensive battery report.

Implements RESL rulebook rules that span MULTIPLE builds of a single battery code:
  - Rule 2.4 / 2.5 / 2.6 / 2.7 / 2.8.1 → Table 1 and Table 2 (qualified builds summary)
  - Rule 3.1 → Table 3 (Performance Degradation Ratio)
  - Rule 3.2 → Table 4 (Temperature Degradation Ratio)
  - Rule 12.0 → Table 12.0 (Composite Design Data)

All computation is server-side: BigQuery SQL + in-process aggregation of per-build
metrics produced by the existing discharge_analysis_tools. The LLM only sees compact
result dictionaries — never raw time-series data.

Also exposes `calculate_material_utilization` as a compatibility alias for
`calculate_active_material_utilization` (the Gemini model sometimes hallucinates the
shorter name).
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Optional

from tools.bigquery_tools import (
    _full_table,
    _get_bq_client,
    analyze_thermal_stack_calorific_value,
    calculate_active_material,
    calculate_active_material_utilization,
)
from tools.discharge_analysis_tools import (
    calculate_activation_time,
    calculate_discharge_duration,
    calculate_on_load_voltage,
    calculate_open_circuit_voltage,
)

# Cap on parallelism for per-build BigQuery fan-out. BigQuery handles concurrent
# queries well; this just bounds resource usage.
_PARALLEL_WORKERS = 12


# ── helpers ────────────────────────────────────────────────


def _list_builds_for_battery(battery_code: str) -> list[str]:
    """Return every build_number present in discharge_data for this battery."""
    client = _get_bq_client()
    sql = f"""
        SELECT DISTINCT build_number
        FROM {_full_table('discharge_data')}
        WHERE battery_code = '{battery_code}'
        ORDER BY build_number
    """
    return [str(row.build_number) for row in client.query(sql).result()]


def _index_by_condition(results: list[dict]) -> dict[tuple, dict]:
    return {
        (r.get("discharge_temperature"), r.get("discharge_type")): r
        for r in (results or [])
    }


def _metrics_for_build(battery_code: str, bn: str) -> list[dict]:
    """Compute the four rulebook metrics for ONE build, returning per-condition rows."""
    dur = calculate_discharge_duration(battery_code, bn)
    if dur.get("status") != "success":
        return []
    act = calculate_activation_time(battery_code, bn)
    ocv = calculate_open_circuit_voltage(battery_code, bn)
    onl = calculate_on_load_voltage(battery_code, bn)

    act_idx = _index_by_condition(act.get("results", []))
    ocv_idx = _index_by_condition(ocv.get("results", []))
    onl_idx = _index_by_condition(onl.get("results", []))

    out: list[dict] = []
    for d in dur.get("results", []):
        key = (d.get("discharge_temperature"), d.get("discharge_type"))
        a = act_idx.get(key, {})
        o = ocv_idx.get(key, {})
        l = onl_idx.get(key, {})
        out.append({
            "build_number": bn,
            "discharge_temperature": key[0],
            "discharge_type": key[1],
            "discharge_duration_s": d.get("computed_discharge_duration_seconds"),
            "target_duration_s": d.get("target_duration_seconds"),
            "duration_pass_fail": d.get("pass_fail"),
            "activation_time_ms": a.get("computed_activation_time_ms"),
            "max_ocv_V": o.get("measured_max_open_circuit_voltage_V"),
            "max_on_load_V": l.get("max_on_load_voltage_V"),
        })
    return out


def _gather_per_build_metrics(battery_code: str) -> list[dict]:
    """For every build of the battery, compute all per-condition metrics in parallel.

    Each output row is keyed by (build_number, temperature, discharge_type) and
    carries the four rulebook metrics (duration, activation, OCV, on-load) plus
    the duration pass/fail verdict against target duration (Rule 2.8.1).
    """
    builds = _list_builds_for_battery(battery_code)
    rows: list[dict] = []
    if not builds:
        return rows
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
        for build_rows in ex.map(lambda bn: _metrics_for_build(battery_code, bn), builds):
            rows.extend(build_rows)
    # Stable ordering for deterministic outputs
    rows.sort(key=lambda r: (str(r.get("build_number")), str(r.get("discharge_temperature")), str(r.get("discharge_type"))))
    return rows


# ── Table 1 & 2 — Qualified Builds Report ─────────────────


def generate_qualified_builds_report(battery_code: str) -> dict:
    """Generate Tables 1 and 2 — per-build metrics and by-condition aggregates.

    Table 1: For every build of the battery that met the Minimum Discharge
    Duration (Rule 2.8.1), list build_number, discharge_temperature,
    discharge_type, discharge_duration_s, max_ocv_V, max_on_load_V,
    activation_time_ms.

    Table 2: From Table 1, group by (discharge_temperature, discharge_type)
    and compute count, min/max/median discharge_duration_s, min/max on-load
    voltage and min/max OCV. Only rows from qualified builds are used.

    Args:
        battery_code: Battery identifier (e.g. '44').

    Returns:
        dict with `table_1_qualified_per_build`, `table_2_summary_by_condition`,
        `qualified_builds`, and `failed_builds`.
    """
    try:
        metrics = _gather_per_build_metrics(battery_code)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    qualified = [r for r in metrics if r.get("duration_pass_fail") == "PASS"]
    passed_builds = sorted({r["build_number"] for r in qualified})
    failed_builds = sorted({
        r["build_number"]
        for r in metrics
        if r.get("duration_pass_fail") == "FAIL"
        and r["build_number"] not in passed_builds
    })

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in qualified:
        groups[(r["discharge_temperature"], r["discharge_type"])].append(r)

    table_2: list[dict] = []
    for (temp, typ), rs in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        durs = [x["discharge_duration_s"] for x in rs if x["discharge_duration_s"] is not None]
        onls = [x["max_on_load_V"] for x in rs if x["max_on_load_V"] is not None]
        ocvs = [x["max_ocv_V"] for x in rs if x["max_ocv_V"] is not None]
        if not durs:
            continue
        table_2.append({
            "discharge_temperature": temp,
            "discharge_type": typ,
            "count": len(rs),
            "min_duration_s": round(min(durs), 4),
            "max_duration_s": round(max(durs), 4),
            "median_duration_s": round(median(durs), 4),
            "max_on_load_V": round(max(onls), 4) if onls else None,
            "min_on_load_V": round(min(onls), 4) if onls else None,
            "max_ocv_V": round(max(ocvs), 4) if ocvs else None,
            "min_ocv_V": round(min(ocvs), 4) if ocvs else None,
        })

    return {
        "status": "success",
        "battery_code": battery_code,
        "rulebook_reference": "Rules 2.4, 2.5, 2.6, 2.7, 2.8.1",
        "computation": "Server-side per-build aggregation (all data points)",
        "total_builds_analyzed": len({r["build_number"] for r in metrics}),
        "qualified_builds": passed_builds,
        "failed_builds": failed_builds,
        "table_1_qualified_per_build": qualified,
        "table_2_summary_by_condition": table_2,
    }


# ── Table 3 — Rule 3.1 Performance Degradation Ratio ──────


def calculate_performance_degradation_ratio(battery_code: str) -> dict:
    """Rule 3.1 — Performance Degradation Ratio (Table 3).

    For each temperature T, for each dynamic discharge type (anything other
    than 'static'), compute:

        PDR(T, dynamic_type) = median(dynamic_duration_at_T) / median(static_duration_at_T)

    Discharge types are defined per Rule 2.2 (static, vibration, acceleration,
    RV, etc.). Only builds that met the Minimum Discharge Duration (Rule
    2.8.1) contribute to the medians.

    Args:
        battery_code: Battery identifier (e.g. '44').

    Returns:
        dict with `table_3` rows — each having discharge_temperature,
        discharge_type, degradation_ratio, and the underlying medians.
    """
    try:
        metrics = _gather_per_build_metrics(battery_code)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    qualified = [r for r in metrics if r.get("duration_pass_fail") == "PASS"]

    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in qualified:
        v = r.get("discharge_duration_s")
        if v is not None:
            groups[(r["discharge_temperature"], r["discharge_type"])].append(v)

    medians = {k: median(v) for k, v in groups.items() if v}

    temps = sorted({k[0] for k in medians}, key=lambda x: str(x))
    table_3: list[dict] = []
    for temp in temps:
        static_key = (temp, "static")
        if static_key not in medians:
            continue
        static_median = medians[static_key]
        if static_median <= 0:
            continue
        for (t, typ), med in sorted(medians.items()):
            if t != temp or typ == "static":
                continue
            table_3.append({
                "discharge_temperature": temp,
                "discharge_type": typ,
                "degradation_ratio": round(med / static_median, 4),
                "dynamic_median_duration_s": round(med, 4),
                "static_median_duration_s": round(static_median, 4),
            })

    return {
        "status": "success",
        "battery_code": battery_code,
        "rulebook_reference": "Rule 3.1 - Performance Degradation Ratio",
        "definition": "PDR = median(dynamic-type duration at T) / median(static duration at T)",
        "qualified_builds_only": True,
        "table_3": table_3,
    }


# ── Table 4 — Rule 3.2 Temperature Degradation Ratio ──────


def calculate_temperature_degradation_ratio(
    battery_code: str,
    warm_reference_temperature: str = "+55",
) -> dict:
    """Rule 3.2 — Temperature Degradation Ratio (Table 4).

    For each discharge type, for every temperature T that is not the warm
    reference, compute:

        TDR(type, T) = median(duration at T) / median(duration at warm reference)

    Only builds that met the Minimum Discharge Duration (Rule 2.8.1) contribute.

    Args:
        battery_code: Battery identifier.
        warm_reference_temperature: Warm reference temperature string — defaults
            to '+55'. Use the exact label stored in discharge_temperature.

    Returns:
        dict with `table_4` rows — each having discharge_type,
        discharge_temperature, degradation_ratio, and the underlying medians.
    """
    try:
        metrics = _gather_per_build_metrics(battery_code)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    qualified = [r for r in metrics if r.get("duration_pass_fail") == "PASS"]

    groups: dict[tuple, list[float]] = defaultdict(list)
    for r in qualified:
        v = r.get("discharge_duration_s")
        if v is not None:
            groups[(r["discharge_temperature"], r["discharge_type"])].append(v)

    medians = {k: median(v) for k, v in groups.items() if v}

    types = sorted({k[1] for k in medians}, key=lambda x: str(x))
    table_4: list[dict] = []
    for typ in types:
        warm_key = (warm_reference_temperature, typ)
        if warm_key not in medians:
            continue
        warm_median = medians[warm_key]
        if warm_median <= 0:
            continue
        for (t, ty), med in sorted(medians.items()):
            if ty != typ or t == warm_reference_temperature:
                continue
            table_4.append({
                "discharge_type": typ,
                "discharge_temperature": t,
                "degradation_ratio": round(med / warm_median, 4),
                "median_duration_s": round(med, 4),
                "warm_reference_median_s": round(warm_median, 4),
                "warm_reference_temperature": warm_reference_temperature,
            })

    return {
        "status": "success",
        "battery_code": battery_code,
        "rulebook_reference": "Rule 3.2 - Temperature Degradation Ratio",
        "definition": (
            f"TDR = median(duration at T) / median(duration at warm reference "
            f"'{warm_reference_temperature}')"
        ),
        "qualified_builds_only": True,
        "table_4": table_4,
    }


# ── Tables 6.1 / 6.2 — multi-build Anode/Cathode summary ──


def generate_anode_cathode_multibuild_summary(
    battery_code: str,
    only_qualified_builds: bool = True,
) -> dict:
    """Tables 6.1 (Anode) and 6.2 (Cathode) — multi-build active-material summary.

    For every build of the battery, calls calculate_active_material (Rules 4.3
    and 4.4) and lays out the results in two parallel tables:

    Table 6.1 columns — Anode Weight per Electrode (4.3.1), Number of Parallel
        Stack (4.3.2), Total Anode Material (4.3.3), Amount of LiSi (4.3.4).
    Table 6.2 columns — Cathode Weight per Electrode (4.4.1), Number of
        Parallel Stack (4.4.2), Total Cathode Material (4.4.3), Amount of
        FeS2 (4.4.4).

    Args:
        battery_code: Battery identifier.
        only_qualified_builds: If True, limit to builds that meet Rule 2.8.1.

    Returns:
        dict with `table_6_1` and `table_6_2` rows keyed by build_number.
    """
    try:
        all_builds = _list_builds_for_battery(battery_code)
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

    builds_to_process = all_builds
    if only_qualified_builds:
        qb_report = generate_qualified_builds_report(battery_code)
        if qb_report.get("status") == "success":
            builds_to_process = qb_report.get("qualified_builds", []) or all_builds

    table_6_1: list[dict] = []
    table_6_2: list[dict] = []
    errors: list[dict] = []

    for bn in builds_to_process:
        res = calculate_active_material(battery_code, bn)
        if res.get("status") != "success":
            errors.append({"build_number": bn, "error": res.get("error_message")})
            continue
        dp = res.get("design_parameters_used", {})
        a = res.get("anode_calculation", {})
        c = res.get("cathode_calculation", {})
        stacks = dp.get("Stacks in Parallel")
        table_6_1.append({
            "build_number": bn,
            "anode_weight_per_electrode_g": dp.get("Anode Weight per Electrode (g)"),
            "number_of_parallel_stacks": stacks,
            "total_anode_material_g": a.get("step_4_3_3_Total_Anode_Material_g"),
            "amount_of_LiSi_g": a.get("step_4_3_5_Anode_Active_Material_LiSi_g"),
        })
        table_6_2.append({
            "build_number": bn,
            "cathode_weight_per_electrode_g": dp.get("Cathode Weight per Electrode (g)"),
            "number_of_parallel_stacks": stacks,
            "total_cathode_material_g": c.get("step_4_4_3_Total_Cathode_Material_g"),
            "amount_of_FeS2_g": c.get("step_4_4_5_Cathode_Active_Material_FeS2_g"),
        })

    return {
        "status": "success",
        "battery_code": battery_code,
        "rulebook_reference": "Rules 4.3, 4.4 (multi-build aggregation)",
        "only_qualified_builds": only_qualified_builds,
        "builds_processed": builds_to_process,
        "table_6_1_anode": table_6_1,
        "table_6_2_cathode": table_6_2,
        "errors": errors,
    }


# ── Table 12.0 — Rule 12.0 Composite Design Data ──────────


def get_composite_design_data(battery_code: str) -> dict:
    """Rule 12.0 — Composite Design Data (Table 12.0).

    Aggregates every design parameter across every build of a given battery
    code into one wide table: rows are parameter names, columns are build
    numbers. Useful for visually spotting which design knob changed between
    builds.

    Args:
        battery_code: Battery identifier (e.g. '44').

    Returns:
        dict with `builds` (ordered list of build numbers) and `table_12_0`
        rows, each row containing `parameter_name`, `unit`, and a `values`
        dict mapping build_number → parameter_value.
    """
    try:
        client = _get_bq_client()
        sql = f"""
            SELECT build_number, parameter_name, parameter_value, unit
            FROM {_full_table('design_parameters')}
            WHERE battery_code = '{battery_code}'
            ORDER BY parameter_name, build_number
        """
        result = client.query(sql).result()

        by_param: dict[str, dict] = {}
        builds_set: set[str] = set()
        for row in result:
            bn = str(row.build_number)
            pn = row.parameter_name
            builds_set.add(bn)
            if pn not in by_param:
                by_param[pn] = {
                    "parameter_name": pn,
                    "unit": row.unit or "",
                    "values": {},
                }
            by_param[pn]["values"][bn] = row.parameter_value

        builds = sorted(builds_set)
        table = [by_param[k] for k in sorted(by_param)]
        return {
            "status": "success",
            "battery_code": battery_code,
            "rulebook_reference": "Rule 12.0 - Composite Design Data",
            "builds": builds,
            "parameter_count": len(table),
            "table_12_0": table,
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── Compatibility alias for hallucinated tool name ────────


def calculate_material_utilization(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Compatibility wrapper — delegates to calculate_active_material_utilization.

    The Gemini model occasionally hallucinates this shorter name; registering
    this wrapper as a tool lets the call succeed instead of failing with
    'Function calculate_material_utilization is not found in the tools_dict.'

    See calculate_active_material_utilization for the full rulebook description
    (Rules 4.3, 4.4, 4.6, 4.7).
    """
    return calculate_active_material_utilization(
        battery_code, build_number, discharge_temperature
    )


# ── Comprehensive report orchestrator (ALL 12 tables in one call) ──


def _md_table(headers: list[str], rows: list[list]) -> str:
    """Render a tiny markdown table. Cells are stringified verbatim."""
    if not rows:
        return "_No data._\n"
    head = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join(["---"] * len(headers)) + " |"
    body = [
        "| " + " | ".join("" if c is None else str(c) for c in r) + " |"
        for r in rows
    ]
    return "\n".join([head, sep, *body]) + "\n"


def _pivot_for_chart(
    rows: list[dict],
    x_field: str,
    series_field: str,
    value_field: str,
) -> tuple[list[str], list[list]]:
    """Pivot a list of long-form rows into a wide table for the chart renderer.

    Given rows like
        [{x: 'A', series: 'static', value: 1.0},
         {x: 'A', series: 'RV',     value: 0.9},
         {x: 'B', series: 'static', value: 1.1}]

    return headers + body suitable for the frontend's chart block format:
        headers = ['x_field', 'static', 'RV']
        body    = [['A', 1.0, 0.9],
                   ['B', 1.1, '']]
    """
    x_values: list = []
    seen_x: set = set()
    series_values: list = []
    seen_series: set = set()
    for r in rows:
        x = r.get(x_field)
        s = r.get(series_field)
        if x is None or s is None:
            continue
        x_key = str(x)
        s_key = str(s)
        if x_key not in seen_x:
            seen_x.add(x_key)
            x_values.append(x_key)
        if s_key not in seen_series:
            seen_series.add(s_key)
            series_values.append(s_key)

    grid: dict[tuple[str, str], object] = {}
    for r in rows:
        x = r.get(x_field)
        s = r.get(series_field)
        v = r.get(value_field)
        if x is None or s is None or v is None:
            continue
        grid[(str(x), str(s))] = v

    headers = [x_field] + series_values
    body = [
        [x] + [grid.get((x, s), "") for s in series_values]
        for x in x_values
    ]
    return headers, body


def _chart_block(
    chart_type: str,
    title: str,
    headers: list[str],
    body: list[list],
) -> str:
    """Render a fenced ```chart block as JSON for the frontend renderer.

    The frontend's parseChartBlock (MessageBubble.jsx) accepts a JSON object of
    the form:

        {
          "type": "bar",
          "title": "...",
          "xKey": "<first column>",
          "yKeys": ["<remaining columns>"],
          "data": [{"<xKey>": "...", "<series>": <number>}, ...]
        }

    JSON is preferred over the legacy pipe format because (a) it round-trips
    cleanly through any LLM that might re-serialize the response, (b) it
    handles missing values without ambiguity, and (c) Recharts consumes the
    object structure directly.
    """
    import json as _json

    if not body or len(headers) < 2:
        return ""
    x_key = headers[0]
    y_keys = headers[1:]

    data_rows: list[dict] = []
    for row in body:
        d: dict = {x_key: row[0]}
        for i, key in enumerate(y_keys, start=1):
            v = row[i] if i < len(row) else None
            # Empty strings → None so the chart shows a gap rather than NaN
            d[key] = None if (v is None or v == "") else v
        data_rows.append(d)

    payload = {
        "type": chart_type,
        "title": title,
        "xKey": x_key,
        "yKeys": y_keys,
        "data": data_rows,
    }
    return "```chart\n" + _json.dumps(payload, indent=2, default=str) + "\n```\n"


def generate_comprehensive_battery_report(battery_code: str) -> dict:
    """Build the FULL multi-table comprehensive report for a battery code in ONE call.

    This is the single tool to call when the user asks for the standard RESL
    comprehensive report. It runs every required sub-tool server-side, builds
    every table the report template specifies, and returns both structured data
    AND a fully pre-rendered markdown report so the LLM can pass it through
    verbatim without orchestrating dozens of tool calls itself.

    Tables produced (matches RESL_Report template):
        Table 1   — Per-build summary, qualified builds only (Rules 2.4–2.8.1)
        Table 2   — Aggregated by (temp, discharge_type) — derived from Table 1
        Table 3   — Performance Degradation Ratio (Rule 3.1)
        Table 4   — Temperature Degradation Ratio (Rule 3.2)
        Table 5.* — Active material utilization per qualified build (Rules 4.6, 4.7)
        Table 6.1 — Anode multi-build summary (Rule 4.3)
        Table 6.2 — Cathode multi-build summary (Rule 4.4)
        Table 7   — Calorific value per gram per second summary (Rule 9.6)
        Table 9.* — Thermal stack calorific value per build (Rules 7.1–9.7)
        Table 10  — Chart-ready discharge-duration trend across builds (Rule 10)
        Table 11  — Chart-ready degradation-ratio summary (Rule 11)
        Table 12.0 — Composite design data (Rule 12.0)

    Args:
        battery_code: Battery identifier (e.g. '44').

    Returns:
        dict with `tables` (structured data for every table above), `markdown_report`
        (a single markdown document the LLM should display verbatim), and a
        `warnings` list collecting any per-build errors.
    """
    warnings: list[str] = []

    # ── (1) Per-build metrics — parallel ──
    try:
        metrics = _gather_per_build_metrics(battery_code)
    except Exception as e:
        return {"status": "error", "error_message": f"Failed gathering per-build metrics: {e}"}
    if not metrics:
        return {
            "status": "error",
            "error_message": f"No discharge data found for battery_code={battery_code}",
        }

    qualified_rows = [r for r in metrics if r.get("duration_pass_fail") == "PASS"]
    qualified_builds = sorted({r["build_number"] for r in qualified_rows})
    failed_builds = sorted({
        r["build_number"]
        for r in metrics
        if r.get("duration_pass_fail") == "FAIL" and r["build_number"] not in qualified_builds
    })
    all_builds = sorted({r["build_number"] for r in metrics})

    # ── (2) Tables 1 & 2 ──
    qb = generate_qualified_builds_report(battery_code)  # reuses gather; cheap second pass
    table_1 = qb.get("table_1_qualified_per_build", [])
    table_2 = qb.get("table_2_summary_by_condition", [])

    # ── (3) Tables 3 & 4 ──
    pdr = calculate_performance_degradation_ratio(battery_code)
    tdr = calculate_temperature_degradation_ratio(battery_code)
    table_3 = pdr.get("table_3", [])
    table_4 = tdr.get("table_4", [])

    # ── (4) Table 5.x — per-qualified-build active material utilization ──
    def _t5(bn: str):
        r = calculate_active_material_utilization(battery_code, bn)
        return bn, r

    table_5: dict[str, dict] = {}
    if qualified_builds:
        with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
            for bn, r in ex.map(_t5, qualified_builds):
                if r.get("status") == "success":
                    table_5[bn] = {
                        "active_material": r.get("active_material"),
                        "table_5_data": r.get("table_5_data"),
                    }
                else:
                    warnings.append(f"Build {bn} active-material util: {r.get('error_message')}")

    # ── (5) Tables 6.1 & 6.2 ──
    ac = generate_anode_cathode_multibuild_summary(battery_code, only_qualified_builds=True)
    table_6_1 = ac.get("table_6_1_anode", [])
    table_6_2 = ac.get("table_6_2_cathode", [])
    for err in ac.get("errors", []) or []:
        warnings.append(f"Build {err.get('build_number')} anode/cathode: {err.get('error')}")

    # ── (6) Table 9.x — Thermal stack calorific value per build ──
    def _t9(bn: str):
        return bn, analyze_thermal_stack_calorific_value(battery_code, bn)

    table_9: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=_PARALLEL_WORKERS) as ex:
        for bn, r in ex.map(_t9, all_builds):
            if r.get("status") == "success":
                table_9[bn] = r
                for verr in r.get("validation_errors", []) or []:
                    warnings.append(f"Build {bn}: {verr}")
            else:
                warnings.append(f"Build {bn} thermal stack: {r.get('error_message')}")

    # ── (7) Table 7 — Calorific value per gram per second summary (derived from Table 9) ──
    table_7: list[dict] = []
    for bn in all_builds:
        d = table_9.get(bn) or {}
        r9 = d.get("rule_9_stack_weight", {}) if isinstance(d, dict) else {}
        r8 = d.get("rule_8_calorific_value", {}) if isinstance(d, dict) else {}
        table_7.append({
            "build_number": bn,
            "total_cv_stack_cal": r8.get("total_cv_stack"),
            "total_stack_weight_g": r9.get("total_stack_weight_g"),
            "cv_per_gram_stack": r9.get("cv_per_gram_stack"),
            "cv_per_gram_per_As": r9.get("cv_per_gram_per_As"),
            "ampere_seconds_capacity_used": r9.get("ampere_seconds_capacity_used"),
        })

    # ── (8) Table 10 — chart-ready discharge-duration-vs-build trend (Rule 10) ──
    table_10: list[dict] = []
    for r in qualified_rows:
        table_10.append({
            "build_number": r["build_number"],
            "discharge_temperature": r["discharge_temperature"],
            "discharge_type": r["discharge_type"],
            "discharge_duration_s": r["discharge_duration_s"],
            "target_duration_s": r["target_duration_s"],
        })

    # ── (9) Table 11 — chart-ready degradation summary (Rule 11) ──
    table_11 = {
        "performance_degradation": table_3,
        "temperature_degradation": table_4,
    }

    # ── (10) Table 12.0 — Composite design data ──
    cdd = get_composite_design_data(battery_code)
    table_12_0 = cdd.get("table_12_0", [])
    table_12_0_builds = cdd.get("builds", [])

    # ── (11) Render the whole thing as a single markdown document ──
    md: list[str] = []
    md.append(f"# Comprehensive Analysis Report — Battery {battery_code}\n")
    md.append(f"**Builds analyzed:** {len(all_builds)} — {', '.join(all_builds)}")
    md.append(f"**Qualified (PASS Min Discharge Duration):** {len(qualified_builds)} — {', '.join(qualified_builds) or 'none'}")
    md.append(f"**Failed:** {len(failed_builds)} — {', '.join(failed_builds) or 'none'}\n")

    md.append("## Table 1 — Performance Summary of Qualified Builds (Rules 2.4–2.8.1)")
    md.append(_md_table(
        ["Build", "Temp (°C)", "Type", "Duration (s)", "Target (s)", "Status", "Activation (ms)", "Max OCV (V)", "Max On-Load (V)"],
        [[
            r.get("build_number"), r.get("discharge_temperature"), r.get("discharge_type"),
            r.get("discharge_duration_s"), r.get("target_duration_s"), r.get("duration_pass_fail"),
            r.get("activation_time_ms"), r.get("max_ocv_V"), r.get("max_on_load_V"),
        ] for r in table_1],
    ))

    md.append("## Table 2 — Performance Summary by Discharge Condition (qualified builds only)")
    md.append(_md_table(
        ["Temp (°C)", "Type", "Count", "Min Dur (s)", "Max Dur (s)", "Median Dur (s)", "Max On-Load (V)", "Min On-Load (V)", "Max OCV (V)", "Min OCV (V)"],
        [[
            r.get("discharge_temperature"), r.get("discharge_type"), r.get("count"),
            r.get("min_duration_s"), r.get("max_duration_s"), r.get("median_duration_s"),
            r.get("max_on_load_V"), r.get("min_on_load_V"), r.get("max_ocv_V"), r.get("min_ocv_V"),
        ] for r in table_2],
    ))

    md.append("## Table 3 — Performance Degradation Ratio (Rule 3.1)")
    md.append("_PDR = median(dynamic-type duration at T) / median(static duration at T)_\n")
    md.append(_md_table(
        ["Temp (°C)", "Discharge Type", "Degradation Ratio", "Dynamic Median (s)", "Static Median (s)"],
        [[
            r.get("discharge_temperature"), r.get("discharge_type"), r.get("degradation_ratio"),
            r.get("dynamic_median_duration_s"), r.get("static_median_duration_s"),
        ] for r in table_3],
    ))

    md.append("## Table 4 — Temperature Degradation Ratio (Rule 3.2)")
    md.append("_TDR = median(duration at T) / median(duration at warm reference)_\n")
    md.append(_md_table(
        ["Discharge Type", "Temp (°C)", "Degradation Ratio", "Median (s)", "Warm Reference Median (s)", "Warm Ref"],
        [[
            r.get("discharge_type"), r.get("discharge_temperature"), r.get("degradation_ratio"),
            r.get("median_duration_s"), r.get("warm_reference_median_s"), r.get("warm_reference_temperature"),
        ] for r in table_4],
    ))

    md.append("## Table 5 — Active Material Utilization per Qualified Build (Rules 4.6, 4.7)")
    for bn in qualified_builds:
        info = table_5.get(bn)
        if not info:
            md.append(f"### Table 5: Build {bn}\n_Data not available._\n")
            continue
        am = info.get("active_material") or {}
        md.append(f"### Table 5: Build {bn}")
        md.append(f"_LiSi: {am.get('Anode_Active_Material_LiSi_g')} g · FeS2: {am.get('Cathode_Active_Material_FeS2_g')} g_\n")
        md.append(_md_table(
            ["S.No", "V/Cell", "V Battery", "Ampere-Seconds", "As/g LiSi", "As/g FeS2", "Type", "Temp"],
            [[
                r.get("S_No"), r.get("cutoff_voltage_per_cell_V"), r.get("battery_cutoff_voltage_V"),
                r.get("ampere_seconds_capacity"), r.get("As_per_gram_LiSi"), r.get("As_per_gram_FeS2"),
                r.get("discharge_type"), r.get("discharge_temperature"),
            ] for r in (info.get("table_5_data") or [])],
        ))

    md.append("## Table 6.1 — Anode Active Material (LiSi) per Build (Rule 4.3)")
    md.append(_md_table(
        ["Build", "Anode Wt/Electrode (g) [4.3.1]", "Parallel Stacks [4.3.2]", "Total Anode Material (g) [4.3.3]", "Amount of LiSi (g) [4.3.4]"],
        [[
            r.get("build_number"), r.get("anode_weight_per_electrode_g"), r.get("number_of_parallel_stacks"),
            r.get("total_anode_material_g"), r.get("amount_of_LiSi_g"),
        ] for r in table_6_1],
    ))

    md.append("## Table 6.2 — Cathode Active Material (FeS2) per Build (Rule 4.4)")
    md.append(_md_table(
        ["Build", "Cathode Wt/Electrode (g) [4.4.1]", "Parallel Stacks [4.4.2]", "Total Cathode Material (g) [4.4.3]", "Amount of FeS2 (g) [4.4.4]"],
        [[
            r.get("build_number"), r.get("cathode_weight_per_electrode_g"), r.get("number_of_parallel_stacks"),
            r.get("total_cathode_material_g"), r.get("amount_of_FeS2_g"),
        ] for r in table_6_2],
    ))

    md.append("## Table 7 — Calorific Value per Gram per Second Summary (Rule 9.6)")
    md.append(_md_table(
        ["Build", "Total Stack CV (cal)", "Total Stack Weight (g)", "CV/g (cal/g)", "CV/g per A·s", "Capacity Used (A·s)"],
        [[
            r.get("build_number"), r.get("total_cv_stack_cal"), r.get("total_stack_weight_g"),
            r.get("cv_per_gram_stack"), r.get("cv_per_gram_per_As"), r.get("ampere_seconds_capacity_used"),
        ] for r in table_7],
    ))

    md.append("## Table 9 — Thermal Stack Calorific Value per Build (Rules 7.1–9.7)")
    for bn in all_builds:
        d = table_9.get(bn) or {}
        if not isinstance(d, dict) or d.get("status") != "success":
            md.append(f"### Table 9: Build {bn}\n_Data not available._\n")
            continue
        r9 = d.get("rule_9_stack_weight", {}) or {}
        components = r9.get("components", {}) or {}
        md.append(f"### Table 9: Build {bn}")
        md.append(_md_table(
            ["Component", "Total Wt (g)"],
            [[k.split("_", 1)[-1].replace("_", " "), v] for k, v in components.items()],
        ))

    md.append("## Table 10 — Discharge Duration Trend Across Qualified Builds (Rule 10)")
    md.append(_md_table(
        ["Build", "Temp (°C)", "Type", "Duration (s)", "Target (s)"],
        [[
            r.get("build_number"), r.get("discharge_temperature"), r.get("discharge_type"),
            r.get("discharge_duration_s"), r.get("target_duration_s"),
        ] for r in table_10],
    ))
    # Chart 10a: per-build discharge duration grouped by discharge_type
    headers10a, body10a = _pivot_for_chart(
        [{"build_number": r["build_number"], "discharge_type": r["discharge_type"],
          "duration_s": r["discharge_duration_s"]} for r in table_10],
        x_field="build_number",
        series_field="discharge_type",
        value_field="duration_s",
    )
    md.append(_chart_block(
        "line",
        "Discharge Duration (s) vs Build — by Discharge Type",
        headers10a, body10a,
    ))
    # Chart 10b: per-build discharge duration grouped by temperature
    headers10b, body10b = _pivot_for_chart(
        [{"build_number": r["build_number"], "discharge_temperature": r["discharge_temperature"],
          "duration_s": r["discharge_duration_s"]} for r in table_10],
        x_field="build_number",
        series_field="discharge_temperature",
        value_field="duration_s",
    )
    md.append(_chart_block(
        "bar",
        "Discharge Duration (s) vs Build — by Temperature",
        headers10b, body10b,
    ))

    md.append("## Table 11 — Degradation Ratio Summary (Rule 11)")
    # Chart 11a: PDR by (temperature × discharge_type)
    headers11a, body11a = _pivot_for_chart(
        table_3,
        x_field="discharge_temperature",
        series_field="discharge_type",
        value_field="degradation_ratio",
    )
    md.append(_chart_block(
        "bar",
        "Performance Degradation Ratio (Rule 3.1) — by Temperature × Type",
        headers11a, body11a,
    ))
    # Chart 11b: TDR by (discharge_type × temperature)
    headers11b, body11b = _pivot_for_chart(
        table_4,
        x_field="discharge_type",
        series_field="discharge_temperature",
        value_field="degradation_ratio",
    )
    md.append(_chart_block(
        "bar",
        "Temperature Degradation Ratio (Rule 3.2) — by Type × Temperature",
        headers11b, body11b,
    ))

    # Bonus: chart of CV per gram per build (Table 7)
    if any(r.get("cv_per_gram_stack") is not None for r in table_7):
        cv_headers = ["build_number", "cv_per_gram_stack"]
        cv_body = [
            [r["build_number"], r.get("cv_per_gram_stack") or 0]
            for r in table_7
        ]
        md.append(_chart_block(
            "bar",
            "Calorific Value per Gram of Stack (cal/g) — by Build",
            cv_headers, cv_body,
        ))

    md.append("## Table 12.0 — Composite Design Data (Rule 12.0)")
    if table_12_0:
        headers = ["Parameter", "Unit"] + list(table_12_0_builds)
        rows_md = [
            [r.get("parameter_name"), r.get("unit")]
            + [(r.get("values", {}) or {}).get(b, "") for b in table_12_0_builds]
            for r in table_12_0
        ]
        md.append(_md_table(headers, rows_md))
    else:
        md.append("_No design parameters found._\n")

    if warnings:
        md.append("## Warnings")
        for w in warnings:
            md.append(f"- ⚠️ {w}")

    markdown_report = "\n".join(md)

    return {
        "status": "success",
        "battery_code": battery_code,
        "computation": "Server-side orchestrated comprehensive report (single tool call)",
        "qualified_builds": qualified_builds,
        "failed_builds": failed_builds,
        "all_builds": all_builds,
        "tables": {
            "table_1_qualified_per_build": table_1,
            "table_2_summary_by_condition": table_2,
            "table_3_performance_degradation_ratio": table_3,
            "table_4_temperature_degradation_ratio": table_4,
            "table_5_active_material_utilization_per_build": table_5,
            "table_6_1_anode": table_6_1,
            "table_6_2_cathode": table_6_2,
            "table_7_calorific_value_per_gram_per_second": table_7,
            "table_9_thermal_stack_calorific_value_per_build": table_9,
            "table_10_discharge_duration_trend": table_10,
            "table_11_degradation_ratio_charts": table_11,
            "table_12_0_composite_design_data": table_12_0,
            "table_12_0_builds": table_12_0_builds,
        },
        "warnings": warnings,
        "markdown_report": markdown_report,
        "rulebook_references": [
            "Rules 2.4, 2.5, 2.6, 2.7, 2.8.1",
            "Rule 3.1 - Performance Degradation Ratio",
            "Rule 3.2 - Temperature Degradation Ratio",
            "Rules 4.3, 4.4 - Active Material",
            "Rules 4.6, 4.7 - Active Material Utilization",
            "Rules 7.1 - 9.7 - Thermal Stack Calorific Value",
            "Rules 10, 11 - Trend / Degradation Charts",
            "Rule 12.0 - Composite Design Data",
        ],
    }
