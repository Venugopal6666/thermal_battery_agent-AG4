"""BigQuery tools for the ADK agent to query thermal battery data.

Project: thermal-battery-agent-ds1
Dataset: thermal_battery_data
Tables: customer_specs, design_parameters, discharge_data, temperature_data
"""

from __future__ import annotations
from typing import Optional

from google.cloud import bigquery

from config import get_settings

settings = get_settings()
_bq_client: bigquery.Client | None = None


def _get_bq_client() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=settings.bq_project)
    return _bq_client


def _full_table(table: str) -> str:
    return f"`{settings.bq_project}.{settings.bq_dataset}.{table}`"


# Max rows to return to the LLM — less is more for accuracy
MAX_ROWS_FOR_LLM = 100


# ── Generic query ───────────────────────────────────────────


def _is_aggregation_query(sql: str) -> bool:
    """Detect if a SQL query uses aggregation (GROUP BY, SUM, AVG, etc.).
    Aggregation queries naturally return few rows, so they don't need LIMIT."""
    upper = sql.upper()
    agg_keywords = ["GROUP BY", "SUM(", "AVG(", "COUNT(", "MIN(", "MAX(",
                    "HAVING", "ARRAY_AGG(", "STRING_AGG("]
    return any(kw in upper for kw in agg_keywords)


def query_bigquery(sql_query: str) -> dict:
    """Execute a read-only SQL query against the thermal battery BigQuery dataset.

    The dataset contains: customer_specs, design_parameters, discharge_data, temperature_data.
    Project: thermal-battery-agent-ds1, Dataset: thermal_battery_data.

    IMPORTANT: discharge_data is very large — always use WHERE and LIMIT clauses.
    Use SAFE_CAST(parameter_value AS FLOAT64) for numeric operations.

    Args:
        sql_query: A valid BigQuery SQL query string. Must be a SELECT statement only.

    Returns:
        dict with 'status', 'row_count', and 'data' (list of row dicts) or 'error_message'.
    """
    if not sql_query.strip().upper().startswith("SELECT"):
        # Also allow WITH (CTE) queries
        if not sql_query.strip().upper().startswith("WITH"):
            return {"status": "error", "error_message": "Only SELECT/WITH queries are allowed."}

    # Force a LIMIT if none is present — but skip for aggregation queries
    sql_upper = sql_query.strip().upper()
    if "LIMIT" not in sql_upper and not _is_aggregation_query(sql_query):
        sql_query = sql_query.rstrip().rstrip(";") + f" LIMIT {MAX_ROWS_FOR_LLM}"

    try:
        client = _get_bq_client()
        query_job = client.query(sql_query)
        results = query_job.result()

        rows = []
        for row in results:
            row_dict = {}
            for k, v in dict(row.items()).items():
                # Round floats for cleaner output
                if isinstance(v, float):
                    row_dict[k] = round(v, 6)
                else:
                    row_dict[k] = v
            rows.append(row_dict)

        # Hard cap to prevent token overflow
        truncated = len(rows) > MAX_ROWS_FOR_LLM
        capped_rows = rows[:MAX_ROWS_FOR_LLM]

        result = {
            "status": "success",
            "row_count": len(rows),
            "data": capped_rows,
        }
        if truncated:
            result["note"] = f"Results truncated to {MAX_ROWS_FOR_LLM} rows. Use more specific filters or aggregations."
        return result
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── Pre-built query tools ──────────────────────────────────


def get_battery_list() -> dict:
    """Get a list of all batteries in the system with their names and codes.

    Returns:
        dict with 'status' and 'data' containing battery_code and battery_name.
    """
    sql = f"""
        SELECT DISTINCT battery_code, ANY_VALUE(battery_name) AS battery_name
        FROM {_full_table('customer_specs')}
        GROUP BY battery_code
        ORDER BY SAFE_CAST(battery_code AS INT64)
    """
    return query_bigquery(sql)


def get_builds_for_battery(battery_code: str) -> dict:
    """Get all builds for a specific battery.

    Args:
        battery_code: The unique battery identifier (e.g. '1', '2').

    Returns:
        dict with 'status' and 'data' containing build_number list.
    """
    sql = f"""
        SELECT DISTINCT build_number
        FROM {_full_table('design_parameters')}
        WHERE battery_code = '{battery_code}'
        ORDER BY SAFE_CAST(build_number AS INT64)
    """
    return query_bigquery(sql)


def get_customer_specs(battery_code: str) -> dict:
    """Get customer specifications for a specific battery.

    Args:
        battery_code: The unique battery identifier.

    Returns:
        dict with 'status' and 'data' containing parameter_name, parameter_value, unit.
    """
    sql = f"""
        SELECT parameter_name, parameter_value, unit
        FROM {_full_table('customer_specs')}
        WHERE battery_code = '{battery_code}'
        ORDER BY parameter_name
    """
    return query_bigquery(sql)


def get_design_parameters(battery_code: str, build_number: str) -> dict:
    """Get design parameters for a specific battery build.

    Args:
        battery_code: The unique battery identifier.
        build_number: The build identifier (e.g. '1', '2').

    Returns:
        dict with 'status' and 'data' containing parameter_name, parameter_value, unit.
    """
    sql = f"""
        SELECT parameter_name, parameter_value, unit
        FROM {_full_table('design_parameters')}
        WHERE battery_code = '{battery_code}' AND build_number = '{build_number}'
        ORDER BY parameter_name
    """
    return query_bigquery(sql)


def get_discharge_data(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Get discharge test data for a specific battery build.

    Returns sampled time-series data (voltage, current over time).
    The data is sampled evenly to fit within token limits while preserving the curve shape.

    Args:
        battery_code: The unique battery identifier.
        build_number: The build identifier.
        discharge_temperature: Optional temperature filter (e.g. '+55', '-30').
        limit: Maximum number of time-series points to return (default 50, max 80).

    Returns:
        dict with 'status', 'summary' (key statistics), and 'data' (sampled time-series points).
    """
    limit = min(limit, 80)  # Hard cap at 80 points — enough for charts, not too much for LLM

    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    # First, get summary statistics (always accurate, regardless of sampling)
    summary_sql = f"""
        SELECT
            COUNT(*) AS total_data_points,
            MIN(time_seconds) AS start_time,
            MAX(time_seconds) AS end_time,
            MIN(voltage_volts) AS min_voltage,
            MAX(voltage_volts) AS max_voltage,
            AVG(voltage_volts) AS avg_voltage,
            MIN(discharge_current_amps) AS min_current,
            MAX(discharge_current_amps) AS max_current,
            AVG(discharge_current_amps) AS avg_current,
            ANY_VALUE(discharge_temperature) AS discharge_temperature,
            ANY_VALUE(discharge_type) AS discharge_type
        FROM {_full_table('discharge_data')}
        WHERE battery_code = '{battery_code}' AND build_number = '{build_number}'
        {temp_filter}
    """

    # Then get evenly-sampled data points using ROW_NUMBER + MOD
    sampled_sql = f"""
        WITH numbered AS (
            SELECT
                time_seconds,
                voltage_volts,
                discharge_current_amps,
                ROW_NUMBER() OVER (ORDER BY time_seconds) AS rn,
                COUNT(*) OVER () AS total
            FROM {_full_table('discharge_data')}
            WHERE battery_code = '{battery_code}' AND build_number = '{build_number}'
            {temp_filter}
        )
        SELECT time_seconds, voltage_volts, discharge_current_amps
        FROM numbered
        WHERE MOD(rn - 1, GREATEST(CAST(FLOOR(total / {limit}) AS INT64), 1)) = 0
           OR rn = total
        ORDER BY time_seconds
        LIMIT {limit}
    """

    try:
        client = _get_bq_client()

        # Get summary
        summary_result = client.query(summary_sql).result()
        summary = {}
        for row in summary_result:
            summary = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in dict(row.items()).items()}

        # Get sampled data
        data_result = client.query(sampled_sql).result()
        data = []
        for row in data_result:
            point = {}
            for k, v in dict(row.items()).items():
                point[k] = round(v, 4) if isinstance(v, float) else v
            data.append(point)

        return {
            "status": "success",
            "summary": summary,
            "sampled_points": len(data),
            "total_points": summary.get("total_data_points", len(data)),
            "note": f"Data evenly sampled to {len(data)} points from {summary.get('total_data_points', '?')} total. Summary statistics are computed from ALL data points.",
            "data": data,
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def get_temperature_data(
    battery_code: str,
    build_number: str,
    limit: int = 50,
) -> dict:
    """Get temperature sensor readings for a specific battery build.

    Returns sampled time-series data from 3 temperature sensors.

    Args:
        battery_code: The unique battery identifier.
        build_number: The build identifier.
        limit: Maximum number of time-series points to return (default 50, max 80).

    Returns:
        dict with 'status', 'summary', and 'data' containing time_seconds, t1, t2, t3.
    """
    limit = min(limit, 80)

    # Summary statistics
    summary_sql = f"""
        SELECT
            COUNT(*) AS total_data_points,
            MIN(time_seconds) AS start_time,
            MAX(time_seconds) AS end_time,
            MIN(t1) AS min_t1, MAX(t1) AS max_t1, AVG(t1) AS avg_t1,
            MIN(t2) AS min_t2, MAX(t2) AS max_t2, AVG(t2) AS avg_t2,
            MIN(t3) AS min_t3, MAX(t3) AS max_t3, AVG(t3) AS avg_t3
        FROM {_full_table('temperature_data')}
        WHERE battery_code = '{battery_code}' AND build_number = '{build_number}'
    """

    # Evenly sampled data
    sampled_sql = f"""
        WITH numbered AS (
            SELECT
                time_seconds, t1, t2, t3,
                ROW_NUMBER() OVER (ORDER BY time_seconds) AS rn,
                COUNT(*) OVER () AS total
            FROM {_full_table('temperature_data')}
            WHERE battery_code = '{battery_code}' AND build_number = '{build_number}'
        )
        SELECT time_seconds, t1, t2, t3
        FROM numbered
        WHERE MOD(rn - 1, GREATEST(CAST(FLOOR(total / {limit}) AS INT64), 1)) = 0
           OR rn = total
        ORDER BY time_seconds
        LIMIT {limit}
    """

    try:
        client = _get_bq_client()

        summary_result = client.query(summary_sql).result()
        summary = {}
        for row in summary_result:
            summary = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in dict(row.items()).items()}

        data_result = client.query(sampled_sql).result()
        data = []
        for row in data_result:
            point = {}
            for k, v in dict(row.items()).items():
                point[k] = round(v, 2) if isinstance(v, float) else v
            data.append(point)

        return {
            "status": "success",
            "summary": summary,
            "sampled_points": len(data),
            "total_points": summary.get("total_data_points", len(data)),
            "note": f"Data evenly sampled to {len(data)} points. Summary statistics from ALL data.",
            "data": data,
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def compare_builds(battery_code: str, build_numbers: list[str]) -> dict:
    """Compare design parameters across multiple builds of the same battery.

    Args:
        battery_code: The unique battery identifier.
        build_numbers: List of build numbers to compare (e.g. ['1', '2', '3']).

    Returns:
        dict with 'status' and 'data' showing parameter differences across builds.
    """
    build_list = ", ".join(f"'{b}'" for b in build_numbers)
    sql = f"""
        SELECT build_number, parameter_name, parameter_value, unit
        FROM {_full_table('design_parameters')}
        WHERE battery_code = '{battery_code}' AND build_number IN ({build_list})
        ORDER BY parameter_name, SAFE_CAST(build_number AS INT64)
    """
    return query_bigquery(sql)


def get_discharge_summary(battery_code: str) -> dict:
    """Get a summary of discharge tests across all builds for a battery.

    This is a lightweight overview — use this FIRST before fetching detailed data.

    Args:
        battery_code: The unique battery identifier.

    Returns:
        dict with 'status' and 'data' showing per-build statistics:
        build_number, discharge_type, voltage range, current range, duration.
    """
    sql = f"""
        SELECT
            build_number,
            discharge_type,
            discharge_temperature,
            COUNT(*) AS data_points,
            ROUND(MIN(voltage_volts), 4) AS min_voltage,
            ROUND(MAX(voltage_volts), 4) AS max_voltage,
            ROUND(AVG(voltage_volts), 4) AS avg_voltage,
            ROUND(MIN(discharge_current_amps), 4) AS min_current,
            ROUND(MAX(discharge_current_amps), 4) AS max_current,
            ROUND(AVG(discharge_current_amps), 4) AS avg_current,
            ROUND(MAX(time_seconds), 2) AS max_time_seconds
        FROM {_full_table('discharge_data')}
        WHERE battery_code = '{battery_code}'
        GROUP BY build_number, discharge_type, discharge_temperature
        ORDER BY SAFE_CAST(build_number AS INT64), discharge_type
    """
    return query_bigquery(sql)


# ── Generic computation tools (for ANY rulebook calculation) ─


def run_aggregation_query(sql_query: str) -> dict:
    """Execute an aggregation SQL query with NO row limit. Use this for complex
    computations that require processing ALL data points.

    This is the PRIMARY tool for implementing ANY rulebook calculation.
    The LLM should write custom SQL to compute whatever the rule requires.

    IMPORTANT GUIDELINES:
    - MUST contain GROUP BY, SUM, AVG, COUNT, or similar aggregation
    - Always filter by battery_code and build_number in WHERE clause
    - Use CTEs (WITH clauses) for multi-step calculations
    - Results should be aggregated/summarized (not raw rows)
    - Maximum 500 result rows

    PROJECT: thermal-battery-agent-ds1
    DATASET: thermal_battery_data
    TABLES:
      - discharge_data: battery_code, battery_name, build_number, discharge_temperature,
        discharge_type, time_seconds, voltage_volts, discharge_current_amps,
        cutoff_voltage_high_temp, cutoff_voltage_low_temp, target_duration_seconds,
        max_open_circuit_voltage, max_activation_time_ms
      - design_parameters: battery_code, battery_name, build_number, serial_number,
        parameter_name, parameter_value, unit
      - customer_specs: battery_code, battery_name, parameter_name, parameter_value, unit
      - temperature_data: battery_code, battery_name, build_number, time_seconds, t1, t2, t3

    EXAMPLE - Ampere-seconds capacity to a cut-off voltage:
      WITH intervals AS (
        SELECT time_seconds, discharge_current_amps, voltage_volts,
               time_seconds - LAG(time_seconds) OVER (ORDER BY time_seconds) AS dt
        FROM `thermal-battery-agent-ds1.thermal_battery_data.discharge_data`
        WHERE battery_code = '46' AND build_number = '208'
          AND discharge_temperature = '+55'
      )
      SELECT ROUND(SUM(discharge_current_amps * dt), 4) AS ampere_seconds
      FROM intervals
      WHERE voltage_volts >= 20.0  -- cut-off voltage
        AND dt IS NOT NULL

    Args:
        sql_query: A BigQuery SQL query with aggregation. Must be SELECT or WITH.

    Returns:
        dict with 'status', 'row_count', and 'data' (list of row dicts).
    """
    sql_stripped = sql_query.strip().upper()
    if not (sql_stripped.startswith("SELECT") or sql_stripped.startswith("WITH")):
        return {"status": "error", "error_message": "Only SELECT/WITH queries are allowed."}

    # Safety: block DELETE, DROP, INSERT, UPDATE
    for forbidden in ["DELETE", "DROP", "INSERT", "UPDATE", "ALTER", "TRUNCATE", "CREATE"]:
        if forbidden in sql_stripped.split("--")[0]:  # Ignore comments
            # Only block if it's a statement keyword (not in a string or column name)
            import re
            if re.search(rf'\b{forbidden}\b', sql_query.upper().split('--')[0]):
                return {"status": "error", "error_message": f"{forbidden} statements are not allowed."}

    MAX_AGG_ROWS = 500

    try:
        client = _get_bq_client()
        query_job = client.query(sql_query)
        results = query_job.result()

        rows = []
        for row in results:
            row_dict = {}
            for k, v in dict(row.items()).items():
                if isinstance(v, float):
                    row_dict[k] = round(v, 6)
                else:
                    row_dict[k] = v
            rows.append(row_dict)

        if len(rows) > MAX_AGG_ROWS:
            return {
                "status": "error",
                "error_message": f"Query returned {len(rows)} rows, exceeding max of {MAX_AGG_ROWS}. "
                                 "Add more GROUP BY or WHERE filters to reduce results.",
            }

        return {
            "status": "success",
            "row_count": len(rows),
            "computation": "Server-side BigQuery SQL",
            "data": rows,
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def compute_capacity_at_voltage(
    battery_code: str,
    build_number: str,
    cutoff_voltage: float,
    discharge_temperature: Optional[str] = None,
    cutoff_voltage_per_cell: Optional[float] = None,
) -> dict:
    """Compute Ampere-seconds capacity of a battery build at a given cut-off voltage.

    Implements Rule 4.6.4: Capacity = SUM(Current × Time_Interval) for duration
    until battery voltage reaches the specified cut-off voltage.

    This is a GENERIC tool — works for ANY cut-off voltage, not just the customer-specified one.
    Use this for Table-5 (Active Material Utilization) and any capacity calculation.

    Per Rule 4.6.1: The standard per-cell cut-off voltages are 1.2, 1.3, 1.4, 1.5, 1.6 volts.
    Per Rule 4.6.2: Number of cells = "Cells in Series" from Design Data.
    Per Rule 4.6.3: Battery Cut-Off Voltage = Cells in Series × Cut-Off Voltage per Cell.

    You can specify EITHER:
    - cutoff_voltage: The absolute battery cut-off voltage in Volts (e.g. 24.0)
    - cutoff_voltage_per_cell: The per-cell voltage (e.g. 1.4). The tool will look up
      "Cells in Series" from design_parameters and compute the battery voltage automatically.

    Args:
        battery_code: Battery code (e.g. '46')
        build_number: Build number (e.g. '208')
        cutoff_voltage: The cut-off voltage in Volts (e.g. 24.0, 20.0, 16.0).
                        If cutoff_voltage_per_cell is provided, this is ignored.
        discharge_temperature: Optional temperature filter (e.g. '+55')
        cutoff_voltage_per_cell: Optional per-cell cut-off voltage (e.g. 1.2, 1.3, 1.4, 1.5, 1.6).
                                 If provided, looks up Cells in Series and computes battery voltage.

    Returns:
        dict with ampere_seconds capacity, discharge_duration to that voltage,
        and additional statistics.
    """
    # If per-cell voltage is given, compute battery-level voltage from Cells in Series
    cells_in_series = None
    if cutoff_voltage_per_cell is not None:
        try:
            client = _get_bq_client()
            cells_sql = f"""
                SELECT SAFE_CAST(parameter_value AS FLOAT64) AS cells
                FROM {_full_table('design_parameters')}
                WHERE battery_code = '{battery_code}'
                  AND build_number = '{build_number}'
                  AND parameter_name = 'Cells in Series'
                LIMIT 1
            """
            result = client.query(cells_sql).result()
            for row in result:
                cells_in_series = int(row["cells"])  # Always a whole number
            if cells_in_series is None:
                return {"status": "error", "error_message": "Could not find 'Cells in Series' in design_parameters."}
            cutoff_voltage = round(cells_in_series * cutoff_voltage_per_cell, 4)
        except Exception as e:
            return {"status": "error", "error_message": f"Error looking up Cells in Series: {e}"}

    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    sql = f"""
        WITH ordered AS (
            SELECT
                time_seconds,
                voltage_volts,
                discharge_current_amps,
                discharge_temperature,
                discharge_type,
                LAG(time_seconds) OVER (
                    PARTITION BY discharge_temperature, discharge_type
                    ORDER BY time_seconds
                ) AS prev_time,
                LAG(voltage_volts) OVER (
                    PARTITION BY discharge_temperature, discharge_type
                    ORDER BY time_seconds
                ) AS prev_voltage
            FROM {_full_table('discharge_data')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              {temp_filter}
        ),
        -- Find the time when voltage drops below cut-off (after peak)
        peak AS (
            SELECT
                discharge_temperature, discharge_type,
                MIN(CASE WHEN voltage_volts = max_v THEN time_seconds END) AS peak_time
            FROM (
                SELECT *, MAX(voltage_volts) OVER (
                    PARTITION BY discharge_temperature, discharge_type
                ) AS max_v
                FROM ordered
            )
            GROUP BY discharge_temperature, discharge_type
        ),
        cutoff_time AS (
            SELECT
                o.discharge_temperature,
                o.discharge_type,
                -- Last time voltage was at or above cut-off (falling phase)
                MAX(CASE
                    WHEN o.time_seconds > p.peak_time AND o.voltage_volts >= {cutoff_voltage}
                    THEN o.time_seconds
                END) AS time_at_cutoff
            FROM ordered o
            JOIN peak p ON o.discharge_temperature = p.discharge_temperature
                       AND o.discharge_type = p.discharge_type
            GROUP BY o.discharge_temperature, o.discharge_type
        ),
        -- Compute capacity: sum of (current × delta_time) up to cut-off time
        capacity AS (
            SELECT
                o.discharge_temperature,
                o.discharge_type,
                ROUND(SUM(
                    CASE
                        WHEN o.prev_time IS NOT NULL
                         AND o.time_seconds <= COALESCE(c.time_at_cutoff, 999999)
                        THEN o.discharge_current_amps * (o.time_seconds - o.prev_time)
                        ELSE 0
                    END
                ), 4) AS ampere_seconds,
                ROUND(MAX(CASE
                    WHEN o.time_seconds <= COALESCE(c.time_at_cutoff, 999999)
                    THEN o.discharge_current_amps
                END), 4) AS max_current_A,
                ROUND(AVG(CASE
                    WHEN o.time_seconds <= COALESCE(c.time_at_cutoff, 999999)
                         AND o.discharge_current_amps > 0.5
                    THEN o.discharge_current_amps
                END), 4) AS avg_current_A,
                COUNT(CASE
                    WHEN o.time_seconds <= COALESCE(c.time_at_cutoff, 999999)
                    THEN 1
                END) AS data_points_used,
                c.time_at_cutoff AS discharge_duration_s
            FROM ordered o
            LEFT JOIN cutoff_time c ON o.discharge_temperature = c.discharge_temperature
                                   AND o.discharge_type = c.discharge_type
            GROUP BY o.discharge_temperature, o.discharge_type, c.time_at_cutoff
        )
        SELECT * FROM capacity
        ORDER BY discharge_temperature, discharge_type
    """

    try:
        client = _get_bq_client()
        result = client.query(sql).result()

        results = []
        for row in result:
            r = dict(row.items())
            results.append({
                "discharge_temperature": r.get("discharge_temperature"),
                "discharge_type": r.get("discharge_type"),
                "cutoff_voltage_V": cutoff_voltage,
                "ampere_seconds": round(r["ampere_seconds"], 4) if r.get("ampere_seconds") else 0,
                "discharge_duration_to_cutoff_s": round(r["discharge_duration_s"], 4) if r.get("discharge_duration_s") else None,
                "avg_current_A": round(r["avg_current_A"], 4) if r.get("avg_current_A") else None,
                "max_current_A": round(r["max_current_A"], 4) if r.get("max_current_A") else None,
                "data_points_used": r.get("data_points_used"),
            })

        result_dict = {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "cutoff_voltage_V": cutoff_voltage,
            "computation": "Server-side BigQuery SQL (ALL data points)",
            "rulebook_reference": "Rule 4.6.4 - Ampere seconds Capacity",
            "results": results,
        }
        # Include per-cell info if used
        if cutoff_voltage_per_cell is not None:
            result_dict["cutoff_voltage_per_cell_V"] = cutoff_voltage_per_cell
            result_dict["cells_in_series"] = cells_in_series
            result_dict["computation_note"] = (
                f"Battery Cut-Off Voltage = {cells_in_series} cells × "
                f"{cutoff_voltage_per_cell} V/cell = {cutoff_voltage} V (per Rule 4.6.3)"
            )
        return result_dict
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def calculate_active_material(
    battery_code: str,
    build_number: str,
) -> dict:
    """Calculate Anode Active Material (LiSi) and Cathode Active Material (FeS2)
    for a given battery build, per Rulebook Rules 4.3 and 4.4.

    This tool implements the EXACT procedure from the rulebook:

    Anode Active Material (Rule 4.3):
      Step 1 (4.3.1): Get "Anode Weight per Electrode" from Design Data
      Step 2 (4.3.2): Get "Stacks in Parallel" from Design Data
      Step 3 (4.3.3): Total Anode Material = Anode Weight per Electrode × Stacks in Parallel
      Step 4 (4.3.4): Get "Anode Pellet - LiSi: Electrolyte Ratio" (e.g. 80:20 → 80%)
      Step 5 (4.3.5): Anode Active Material = Total Anode Material × (Percentage / 100)

    Cathode Active Material (Rule 4.4):
      Step 1 (4.4.1): Get "Cathode Weight per Electrode" from Design Data
      Step 2 (4.4.2): Get "Stacks in Parallel" from Design Data
      Step 3 (4.4.3): Total Cathode Material = Cathode Weight per Electrode × Stacks in Parallel
      Step 4 (4.4.4): Get "Cathode Pellet - FeS2: Electrolyte Ratio" (e.g. 73.5:25 → 73.5%)
      Step 5 (4.4.5): Cathode Active Material = Total Cathode Material × (Percentage / 100)

    Args:
        battery_code: The battery type code (e.g., "46")
        build_number: The build number (e.g., "208")

    Returns:
        dict with detailed calculation breakdown and results.
    """
    import re

    try:
        client = _get_bq_client()

        # Fetch all required design parameters in one query
        param_names = [
            "Anode Weight per Electrode",
            "Cathode Weight per Electrode",
            "Stacks in Parallel",
            "Anode Pellet - LiSi: Electrolyte Ratio",
            "Cathode Pellet - FeS2: Electrolyte Ratio",
        ]
        placeholders = ", ".join(f"'{p}'" for p in param_names)
        sql = f"""
            SELECT parameter_name, parameter_value, unit
            FROM {_full_table('design_parameters')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              AND parameter_name IN ({placeholders})
        """
        result = client.query(sql).result()
        params = {}
        for row in result:
            params[row["parameter_name"]] = row["parameter_value"]

        # Validate all required params exist
        missing = [p for p in param_names if p not in params or not params[p]]
        if missing:
            return {
                "status": "error",
                "error_message": f"Missing design parameters: {missing}. Check battery_code={battery_code}, build_number={build_number}.",
            }

        # Extract numeric values
        anode_weight = float(params["Anode Weight per Electrode"])
        cathode_weight = float(params["Cathode Weight per Electrode"])
        stacks_parallel = int(float(params["Stacks in Parallel"]))

        # Parse ratio strings: "(80:20)" → 80.0
        anode_ratio_str = params["Anode Pellet - LiSi: Electrolyte Ratio"]
        cathode_ratio_str = params["Cathode Pellet - FeS2: Electrolyte Ratio"]

        anode_match = re.search(r'([\d.]+)\s*:\s*([\d.]+)', anode_ratio_str)
        cathode_match = re.search(r'([\d.]+)\s*:\s*([\d.]+)', cathode_ratio_str)

        if not anode_match:
            return {"status": "error", "error_message": f"Cannot parse Anode ratio: '{anode_ratio_str}'"}
        if not cathode_match:
            return {"status": "error", "error_message": f"Cannot parse Cathode ratio: '{cathode_ratio_str}'"}

        anode_active_pct = float(anode_match.group(1))   # e.g. 80.0
        cathode_active_pct = float(cathode_match.group(1))  # e.g. 73.5

        # Rule 4.3.3: Total Anode Material
        total_anode = anode_weight * stacks_parallel

        # Rule 4.3.5: Anode Active Material (LiSi)
        anode_active = total_anode * (anode_active_pct / 100.0)

        # Rule 4.4.3: Total Cathode Material
        total_cathode = cathode_weight * stacks_parallel

        # Rule 4.4.5: Cathode Active Material (FeS2)
        cathode_active = total_cathode * (cathode_active_pct / 100.0)

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (design_parameters → active material)",
            "design_parameters_used": {
                "Anode Weight per Electrode (g)": anode_weight,
                "Cathode Weight per Electrode (g)": cathode_weight,
                "Stacks in Parallel": stacks_parallel,
                "Anode Pellet - LiSi: Electrolyte Ratio": anode_ratio_str,
                "Cathode Pellet - FeS2: Electrolyte Ratio": cathode_ratio_str,
            },
            "anode_calculation": {
                "rule_reference": "Rule 4.3",
                "step_4_3_3_Total_Anode_Material_g": round(total_anode, 6),
                "step_4_3_3_formula": f"{anode_weight} g × {stacks_parallel} = {round(total_anode, 6)} g",
                "step_4_3_4_LiSi_percentage": anode_active_pct,
                "step_4_3_5_Anode_Active_Material_LiSi_g": round(anode_active, 6),
                "step_4_3_5_formula": f"{round(total_anode, 6)} g × {anode_active_pct / 100} = {round(anode_active, 6)} g",
            },
            "cathode_calculation": {
                "rule_reference": "Rule 4.4",
                "step_4_4_3_Total_Cathode_Material_g": round(total_cathode, 6),
                "step_4_4_3_formula": f"{cathode_weight} g × {stacks_parallel} = {round(total_cathode, 6)} g",
                "step_4_4_4_FeS2_percentage": cathode_active_pct,
                "step_4_4_5_Cathode_Active_Material_FeS2_g": round(cathode_active, 6),
                "step_4_4_5_formula": f"{round(total_cathode, 6)} g × {cathode_active_pct / 100} = {round(cathode_active, 6)} g",
            },
            "summary": {
                "Anode_Active_Material_LiSi_g": round(anode_active, 6),
                "Cathode_Active_Material_FeS2_g": round(cathode_active, 6),
            },
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def calculate_active_material_utilization(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Calculate Ampere-seconds per gram of LiSi and FeS2 for Table-5/Table-6
    (Active Material Utilization), per Rulebook Rules 4.6 and 4.7.

    This tool does everything in ONE call:
    1. Computes Anode Active Material (LiSi) and Cathode Active Material (FeS2) per Rules 4.3, 4.4
    2. For each of the 5 standard cut-off voltages (1.2, 1.3, 1.4, 1.5, 1.6 V/cell per Rule 4.6.1):
       - Computes Ampere-seconds capacity (Rule 4.6.4)
       - Computes Ampere-seconds per gram of LiSi (Rule 4.7.1)
       - Computes Ampere-seconds per gram of FeS2 (Rule 4.7.2)

    Args:
        battery_code: Battery code (e.g., "46")
        build_number: Build number (e.g., "208")
        discharge_temperature: Optional temperature filter (e.g., "+55")

    Returns:
        dict with complete Table-5 data for active material utilization.
    """
    # Step 1: Get active material
    active_mat = calculate_active_material(battery_code, build_number)
    if active_mat.get("status") != "success":
        return active_mat

    lisi_g = active_mat["summary"]["Anode_Active_Material_LiSi_g"]
    fes2_g = active_mat["summary"]["Cathode_Active_Material_FeS2_g"]

    # Step 2: Compute capacity at each standard per-cell voltage (Rule 4.6.1)
    per_cell_voltages = [1.2, 1.3, 1.4, 1.5, 1.6]
    table_rows = []

    for i, v_per_cell in enumerate(per_cell_voltages, start=1):
        cap_result = compute_capacity_at_voltage(
            battery_code=battery_code,
            build_number=build_number,
            cutoff_voltage=0,  # Will be overridden by cutoff_voltage_per_cell
            discharge_temperature=discharge_temperature,
            cutoff_voltage_per_cell=v_per_cell,
        )

        if cap_result.get("status") != "success":
            table_rows.append({
                "S_No": i,
                "cutoff_voltage_per_cell_V": v_per_cell,
                "error": cap_result.get("error_message", "Unknown error"),
            })
            continue

        battery_voltage = cap_result.get("cutoff_voltage_V", 0)
        cells = cap_result.get("cells_in_series", 0)

        # Process each discharge condition
        for res in cap_result.get("results", []):
            ampere_seconds = res.get("ampere_seconds", 0)

            # Rule 4.7.1: As per gram of LiSi
            as_per_gram_lisi = round(ampere_seconds / lisi_g, 4) if lisi_g > 0 else 0
            # Rule 4.7.2: As per gram of FeS2
            as_per_gram_fes2 = round(ampere_seconds / fes2_g, 4) if fes2_g > 0 else 0

            table_rows.append({
                "S_No": i,
                "cutoff_voltage_per_cell_V": v_per_cell,
                "cells_in_series": cells,
                "battery_cutoff_voltage_V": battery_voltage,
                "ampere_seconds_capacity": round(ampere_seconds, 4),
                "As_per_gram_LiSi": as_per_gram_lisi,
                "As_per_gram_FeS2": as_per_gram_fes2,
                "discharge_temperature": res.get("discharge_temperature"),
                "discharge_type": res.get("discharge_type"),
            })

    return {
        "status": "success",
        "battery_code": battery_code,
        "build_number": build_number,
        "computation": "Server-side (Rules 4.3, 4.4, 4.6, 4.7)",
        "active_material": {
            "Anode_Active_Material_LiSi_g": lisi_g,
            "Cathode_Active_Material_FeS2_g": fes2_g,
        },
        "design_parameters": active_mat.get("design_parameters_used", {}),
        "table_5_data": table_rows,
        "rulebook_references": {
            "Rule 4.3": "Anode Active Material (LiSi) calculation",
            "Rule 4.4": "Cathode Active Material (FeS2) calculation",
            "Rule 4.6.1": "Standard per-cell cut-off voltages: 1.2, 1.3, 1.4, 1.5, 1.6 V",
            "Rule 4.6.4": "Ampere-seconds capacity = SUM(Current × Time_Interval) to cut-off",
            "Rule 4.7.1": "As/g LiSi = Ampere-seconds / Anode Active Material",
            "Rule 4.7.2": "As/g FeS2 = Ampere-seconds / Cathode Active Material",
        },
    }

def analyze_thermal_stack_calorific_value(
    battery_code: str,
    build_number: str,
) -> dict:
    """Computes Thermal Stack Calorific Value and Component Weights 
    according to Rulebook Rules 7.1, 8.0, and 9.0.

    Args:
        battery_code: The battery type code (e.g., "46")
        build_number: The build number (e.g., "208")

    Returns:
        dict with verification results, weights breakdown, and final calorific values.
    """
    try:
        client = _get_bq_client()

        param_names = [
            "Cells in Series", "Stacks in Parallel", "Total Numbers of Cells",
            "Total Number of Cells", "Heat Pellet-1 Qty", "Heat Pellet-1B Qty",
            "Heat Pellet-1 Weight", "Heat Pellet-1B Weight", "HP-1 CV/Pellet",
            "Anode Weight per Electrode", "Cathode Weight per Electrode",
            "Electrolyte Weight per Electrode", "Current Collector Weight"
        ]
        placeholders = ", ".join(f"'{p}'" for p in param_names)
        sql = f"""
            SELECT parameter_name, parameter_value
            FROM {_full_table('design_parameters')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              AND parameter_name IN ({placeholders})
        """
        results = client.query(sql).result()
        params = {}
        for row in results:
            if row.parameter_value:
                # Store numeric floats / ints safely
                try:
                    params[row.parameter_name] = float(row.parameter_value)
                except ValueError:
                    params[row.parameter_name] = 0.0

        # Helper to grab param with fallback
        def get_p(key: str, fallback: float = 0.0) -> float:
            return float(params.get(key, fallback))

        def get_i(key: str, fallback: int = 0) -> int:
            return int(params.get(key, fallback))

        cells = get_i("Cells in Series")
        stacks = get_i("Stacks in Parallel", 1)
        
        total_cells_db = get_i("Total Numbers of Cells", get_i("Total Number of Cells"))
        
        hp1_qty = get_i("Heat Pellet-1 Qty")
        hp1b_qty = get_i("Heat Pellet-1B Qty")

        # --- Rule 7.1 Validation ---
        errors = []
        if total_cells_db > 0 and total_cells_db != (cells * stacks):
            errors.append(f"Rule 7.1.2 ERROR: Total Number of Cells ({total_cells_db}) does not match Number of Cells in Series ({cells}) multiplied by Number of Stacks in Parallel ({stacks}).")
        
        if total_cells_db > 0 and total_cells_db != (hp1_qty + hp1b_qty):
            errors.append(f"Rule 7.1.3 ERROR: Total Number of Cells ({total_cells_db}) does not match the sum of Heat Pellet-1 Qty ({hp1_qty}) and Heat Pellet-1B Qty ({hp1b_qty}).")
            
        hp1_wt = get_p("Heat Pellet-1 Weight")
        hp1b_wt = get_p("Heat Pellet-1B Weight")
        hp1_cv = get_p("HP-1 CV/Pellet")
        
        # --- Rule 8.0: Calorific Value (Stack) ---
        total_cv_hp1 = hp1_qty * hp1_wt * hp1_cv
        total_cv_hp1b = hp1b_qty * hp1b_wt * hp1_cv
        total_cv_stack = total_cv_hp1 + total_cv_hp1b

        # --- Rule 9.0: Weight of Stack ---
        anode_wt = get_p("Anode Weight per Electrode")
        cathode_wt = get_p("Cathode Weight per Electrode")
        electrolyte_wt = get_p("Electrolyte Weight per Electrode")
        cc_wt = get_p("Current Collector Weight")
        
        cs_sp = cells * stacks

        stack_wts = {
            "1_Anode": round(cs_sp * anode_wt, 4),
            "2_Cathode": round(cs_sp * cathode_wt, 4),
            "3_Electrolyte": round(cs_sp * electrolyte_wt, 4),
            "4_Heat_Pellet_1": round(cs_sp * hp1_wt, 4),
            "5_Heat_Pellet_1B": round(cs_sp * hp1b_wt, 4),
            "6_Current_Collector": round(cs_sp * cc_wt * 2.0, 4)
        }
        
        total_stack_weight = sum(stack_wts.values())
        
        cv_per_gram = round(total_cv_stack / total_stack_weight, 4) if total_stack_weight > 0 else 0.0
        
        # Fetching max Ampere capacity by reusing the Table-5 calculation function
        util_res = calculate_active_material_utilization(battery_code, build_number)
        as_capacity = 1.0 # fallback
        if util_res.get("status") == "success":
            # get maximum ampere seconds capacity from the table rows
            table_5_data = util_res.get("table_5_data", [])
            for row in table_5_data:
                as_capacity = max(as_capacity, row.get("ampere_seconds_capacity", 0.0))
        
        cv_per_gram_per_as = round(cv_per_gram / float(as_capacity), 6) if as_capacity else 0.0

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (Rules 7.1, 8.0, 9.0)",
            "validation_errors": errors,
            "validation_passed": len(errors) == 0,
            "rule_8_calorific_value": {
                "total_cv_hp1": round(total_cv_hp1, 4),
                "total_cv_hp1b": round(total_cv_hp1b, 4),
                "total_cv_stack": round(total_cv_stack, 4)
            },
            "rule_9_stack_weight": {
                "total_stack_weight_g": round(total_stack_weight, 4),
                "components": stack_wts,
                "cv_per_gram_stack": cv_per_gram,
                "cv_per_gram_per_As": cv_per_gram_per_as,
                "ampere_seconds_capacity_used": round(float(as_capacity), 4)
            },
            "rulebook_references": {
                "Rule 7.1": "Total Cells verification against Series/Parallel and Pellet Quantities",
                "Rule 8.1": "Stack Calorific Value calculation",
                "Rule 9.1-9.4": "Stack Component Weights",
                "Rule 9.5-9.6": "CV per gram and CV per gram per Ampere Second"
            }
        }
    except Exception as e:
        return {"status": "error", "error_message": str(e)}
