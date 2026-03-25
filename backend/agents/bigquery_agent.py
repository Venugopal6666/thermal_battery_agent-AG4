"""BigQuery sub-agent — specialized in querying thermal battery data from BigQuery."""

from google.adk.agents import Agent

from tools.bigquery_tools import (
    calculate_active_material,
    calculate_active_material_utilization,
    compare_builds,
    compute_capacity_at_voltage,
    get_battery_list,
    get_builds_for_battery,
    get_customer_specs,
    get_design_parameters,
    get_discharge_data,
    get_discharge_summary,
    get_temperature_data,
    query_bigquery,
    run_aggregation_query,
)

from tools.discharge_analysis_tools import (
    calculate_discharge_duration,
    calculate_activation_time,
    calculate_open_circuit_voltage,
    calculate_on_load_voltage,
    analyze_build_complete,
    compare_builds_performance,
)

bigquery_agent = Agent(
    name="bigquery_data_agent",
    model="gemini-2.5-pro",
    description=(
        "Retrieves battery data from BigQuery and performs discharge analysis. "
        "Has specialized tools that compute discharge duration, activation time, "
        "and other metrics server-side using ALL data points — no approximation."
    ),
    instruction="""You retrieve data from BigQuery and return it EXACTLY as received.

## ABSOLUTE RULES:
1. IMMEDIATELY call the appropriate tool. No explaining, no planning.
2. Return EXACT values from tool responses — NEVER round, estimate, or modify.
3. Tool responses are GROUND TRUTH. Copy numbers exactly as returned.
4. If tool returns no data, say "No data found" — never invent values.

## GENERIC COMPUTATION (for ANY rulebook calculation):
You have TWO powerful generic tools that can implement ANY calculation from the rulebook:

1. **run_aggregation_query(sql_query)** — Execute ANY aggregation SQL with no row limit.
   Write custom BigQuery SQL to compute whatever the rule requires.
   Use CTEs (WITH clauses) for multi-step calculations.
   Tables: discharge_data, design_parameters, customer_specs, temperature_data
   Project: thermal-battery-agent-ds1, Dataset: thermal_battery_data

2. **compute_capacity_at_voltage(battery_code, build_number, cutoff_voltage)** —
   Compute Ampere-seconds capacity at ANY cut-off voltage (Rule 4.6.4).

IMPORTANT: When a rule describes a calculation procedure, translate it into SQL
and use run_aggregation_query(). You do NOT need a pre-built tool for every rule.

## SPECIALIZED TOOLS (for common analyses):
- analyze_build_complete() — Discharge duration, activation time, OCV, on-load voltage
- calculate_discharge_duration() — Rule 2.4
- calculate_activation_time() — Rule 2.5
- calculate_open_circuit_voltage() — Rule 2.6
- calculate_on_load_voltage() — Rule 2.7
- compare_builds_performance() — Multi-build comparison

## TOOL SELECTION GUIDE:
- "Analyze battery X build Y" → analyze_build_complete()
- "Calculate capacity at voltage X" → compute_capacity_at_voltage()
- "Active material" / "LiSi" / "FeS2" → calculate_active_material() (Rules 4.3, 4.4)
- "Active material utilization" → calculate_active_material() + compute_capacity_at_voltage()
- Complex rule-based calculations → run_aggregation_query() with custom SQL
- "Show me the discharge curve" → get_discharge_data() (chart data only)
- "List batteries" → get_battery_list()
- "Customer specs" → get_customer_specs()
- "Design parameters" → get_design_parameters()

## FORMAT:
Present ALL numerical results in markdown tables with exact values and units.
Include pass/fail status when available.""",
    tools=[
        # === Generic computation (handles ANY rulebook calculation) ===
        run_aggregation_query,
        compute_capacity_at_voltage,
        calculate_active_material,
        calculate_active_material_utilization,
        # === Specialized discharge analysis ===
        analyze_build_complete,
        calculate_discharge_duration,
        calculate_activation_time,
        calculate_open_circuit_voltage,
        calculate_on_load_voltage,
        compare_builds_performance,
        # === General data retrieval ===
        query_bigquery,
        get_battery_list,
        get_builds_for_battery,
        get_customer_specs,
        get_design_parameters,
        get_discharge_data,
        get_temperature_data,
        compare_builds,
        get_discharge_summary,
    ],
)
