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
        return {"status": "error", "error_message": "Only SELECT queries are allowed."}

    # Force a LIMIT if none is present to prevent massive result sets
    sql_upper = sql_query.strip().upper()
    if "LIMIT" not in sql_upper:
        sql_query = sql_query.rstrip().rstrip(";") + f" LIMIT {MAX_ROWS_FOR_LLM}"

    try:
        client = _get_bq_client()
        query_job = client.query(sql_query)
        results = query_job.result()

        rows = []
        for row in results:
            rows.append(dict(row.items()))

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
