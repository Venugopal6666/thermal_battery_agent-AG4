"""Analysis sub-agent — electrochemistry & physics analysis with data access."""

from google.adk.agents import Agent

from tools.bigquery_tools import (
    get_discharge_data,
    get_temperature_data,
    get_customer_specs,
    get_design_parameters,
    get_discharge_summary,
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

from tools.calculation_tools import (
    analyze_discharge_curve,
    analyze_temperature_profile,
    calculate_specific_energy,
    calculate_energy_density,
    calculate_c_rate,
    calculate_thermal_efficiency,
    calculate_internal_resistance,
)

analysis_agent = Agent(
    name="analysis_agent",
    model="gemini-2.5-flash",
    description=(
        "Performs thermal battery analysis using server-side computation tools. "
        "Has specialized discharge analysis tools that compute metrics from ALL "
        "data points, plus physics calculation tools. Use for any analysis question."
    ),
    instruction="""You analyze thermal battery data using server-side computation tools.

## ABSOLUTE RULES:
1. CALL tools IMMEDIATELY. Never describe what you would do.
2. For discharge analysis: ALWAYS use analyze_build_complete() or the specific
   discharge analysis tools. They compute from ALL data points server-side.
3. NEVER do mental math. Use calculation tools for ALL computations.
4. Report EXACT values from tool responses. Never round or estimate.

## PRIMARY TOOLS (use these for discharge analysis):
- analyze_build_complete(battery_code, build_number) — BEST CHOICE for complete analysis
  Returns: discharge_duration, activation_time, OCV, on-load voltage, pass/fail, stats
- calculate_discharge_duration(battery_code, build_number) — Rule 2.4
- calculate_activation_time(battery_code, build_number) — Rule 2.5
- calculate_open_circuit_voltage(battery_code, build_number) — Rule 2.6
- calculate_on_load_voltage(battery_code, build_number) — Rule 2.7
- compare_builds_performance(battery_code, build_numbers) — Multi-build comparison

## SECONDARY TOOLS (for additional calculations):
- calculate_specific_energy, calculate_energy_density, calculate_c_rate, etc.

## IMPORTANT:
The discharge analysis tools implement the RESL rulebook procedures server-side.
They process ALL data points (millions) via BigQuery SQL. The results include:
- Exact discharge duration with linear interpolation (Rule 2.4.3)
- Pass/fail status against customer specs (Rule 2.8.1)
- All relevant cut-off voltages used

## RESPONSE FORMAT:
### Summary
Brief answer with exact numbers.

### Results Table
| Metric | Value | Unit | Status |
|--------|-------|------|--------|
| Discharge Duration | 45.2134 | s | PASS |

### Rule Compliance (if rules provided)
| Rule | Requirement | Actual | Status |
|------|-------------|--------|--------|

### Analysis
Interpretation with exact numbers from tool responses.""",
    tools=[
        # Specialized discharge analysis
        analyze_build_complete,
        calculate_discharge_duration,
        calculate_activation_time,
        calculate_open_circuit_voltage,
        calculate_on_load_voltage,
        compare_builds_performance,
        # Data retrieval
        get_discharge_data,
        get_temperature_data,
        get_customer_specs,
        get_design_parameters,
        get_discharge_summary,
        query_bigquery,
        # Physics calculations
        analyze_discharge_curve,
        analyze_temperature_profile,
        calculate_specific_energy,
        calculate_energy_density,
        calculate_c_rate,
        calculate_thermal_efficiency,
        calculate_internal_resistance,
    ],
)
