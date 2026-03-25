"""Deep Research sub-agent — multi-step investigation across battery builds."""

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

from tools.calculation_tools import (
    analyze_discharge_curve,
    analyze_temperature_profile,
    calculate_c_rate,
    calculate_energy_density,
    calculate_internal_resistance,
    calculate_specific_energy,
    calculate_thermal_efficiency,
)
from tools.rulebook_tools import search_rules

research_agent = Agent(
    name="deep_research_agent",
    model="gemini-2.5-pro",
    description=(
        "Conducts deep research across multiple batteries and builds. "
        "Has ALL tools including specialized discharge analysis that computes "
        "from ALL data points server-side. Use for complex multi-build analysis."
    ),
    instruction="""You are a senior thermal battery research analyst with ALL tools.

## ABSOLUTE RULES:
1. CALL TOOLS IMMEDIATELY. Never describe your plan.
2. Every number MUST come from a tool response. Zero exceptions.
3. For discharge analysis: ALWAYS use analyze_build_complete() — it computes
   all metrics server-side from ALL data points using rulebook procedures.
4. Fetch data ONE build at a time.

## PRIMARY ANALYSIS TOOLS:
- analyze_build_complete(battery_code, build_number) — COMPLETE analysis per rulebook
- compare_builds_performance(battery_code, build_numbers) — Multi-build comparison
- calculate_discharge_duration / calculate_activation_time — Individual metrics

These tools implement Rules 2.4-2.8 server-side with full precision.

## EXECUTION ORDER (silent):
1. get_customer_specs() — Get customer requirements
2. analyze_build_complete() — Compute all metrics per build
3. compare_builds_performance() — Compare if multiple builds
4. Check rules against results
5. Present structured report

## FINAL REPORT FORMAT:

### Executive Summary
Key findings with EXACT numbers.

### Metrics Table
| Build | Duration (s) | Target (s) | Status | Activation (ms) | Max OCV (V) |
|-------|-------------|------------|--------|-----------------|-------------|
| 108   | 45.2134     | 40.0       | PASS   | 312.45           | 28.5432     |

### Rule Compliance
| Rule | Requirement | Actual | Status |
|------|-------------|--------|--------|

### Key Findings
Specific observations with EXACT values.

### Recommendations
Actionable next steps.""",
    tools=[
        # Generic computation (handles ANY rulebook calculation)
        run_aggregation_query,
        compute_capacity_at_voltage,
        calculate_active_material,
        calculate_active_material_utilization,
        # Specialized discharge analysis (PRIMARY)
        analyze_build_complete,
        compare_builds_performance,
        calculate_discharge_duration,
        calculate_activation_time,
        calculate_open_circuit_voltage,
        calculate_on_load_voltage,
        # Data retrieval
        query_bigquery,
        get_battery_list,
        get_builds_for_battery,
        get_customer_specs,
        get_design_parameters,
        get_discharge_data,
        get_temperature_data,
        compare_builds,
        get_discharge_summary,
        # Physics calculations
        analyze_discharge_curve,
        analyze_temperature_profile,
        calculate_specific_energy,
        calculate_energy_density,
        calculate_c_rate,
        calculate_thermal_efficiency,
        calculate_internal_resistance,
        # Rules
        search_rules,
    ],
)
