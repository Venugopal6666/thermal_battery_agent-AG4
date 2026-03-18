"""BigQuery sub-agent — specialized in querying thermal battery data from BigQuery."""

from google.adk.agents import Agent

from tools.bigquery_tools import (
    compare_builds,
    get_battery_list,
    get_builds_for_battery,
    get_customer_specs,
    get_design_parameters,
    get_discharge_data,
    get_discharge_summary,
    get_temperature_data,
    query_bigquery,
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
    model="gemini-2.5-flash",
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

## CRITICAL: DISCHARGE ANALYSIS TOOLS
For ANY question about discharge data, duration, activation time, voltage analysis:
- Use analyze_build_complete() for comprehensive analysis of a single build
- Use calculate_discharge_duration() for discharge duration specifically
- Use calculate_activation_time() for activation time specifically
- Use calculate_open_circuit_voltage() for OCV analysis
- Use calculate_on_load_voltage() for on-load voltage
- Use compare_builds_performance() to compare multiple builds

These tools compute results from ALL 3.8M+ data points using BigQuery SQL.
They implement the RESL rulebook procedures (Rules 2.4-2.8) server-side.
The results are EXACT — no sampling, no estimation, no approximation.

NEVER use get_discharge_data() for analysis — it only returns sampled points.
Use get_discharge_data() ONLY when user wants to see raw time-series for charts.

## TOOL SELECTION GUIDE:
- "Analyze battery X build Y" → analyze_build_complete()
- "What is the discharge duration?" → calculate_discharge_duration()
- "What is the activation time?" → calculate_activation_time()
- "Compare builds A, B, C" → compare_builds_performance()
- "Show me the discharge curve" → get_discharge_data() (for chart data only)
- "List batteries" → get_battery_list()
- "Customer specs" → get_customer_specs()
- "Design parameters" → get_design_parameters()
- "Overview/summary" → get_discharge_summary()

## FORMAT:
Present ALL numerical results in markdown tables with exact values and units.
Include pass/fail status when available.""",
    tools=[
        # === Specialized discharge analysis (preferred for analysis) ===
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
