"""Specialized discharge analysis tools — all computation happens server-side.

These tools implement the RESL rulebook calculation procedures directly in
BigQuery SQL and Python. The LLM receives ONLY the final computed results,
never the raw millions of data points.

Architecture:
  3.8M rows in BigQuery → SQL aggregation → Python computation → compact result dict → LLM
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


def _interpolate_time_at_voltage(
    time_before: float, voltage_before: float,
    time_after: float, voltage_after: float,
    target_voltage: float
) -> float:
    """Linear interpolation: find exact time when voltage = target_voltage.
    
    Implements Rule 2.4.3: "If the exact Cut-Off-Voltage occurs between two
    time intervals, extrapolate assuming linear voltage degradation."
    """
    if abs(voltage_after - voltage_before) < 1e-9:
        return time_before
    fraction = (target_voltage - voltage_before) / (voltage_after - voltage_before)
    return round(time_before + fraction * (time_after - time_before), 4)


# ── 1. Discharge Duration (Rule 2.4) ───────────────────────

def calculate_discharge_duration(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Calculate EXACT discharge duration following RESL Rulebook Rule 2.4.

    This implements the complete rulebook procedure:
    - Rule 2.4.1: Find when voltage reaches cut-off voltage (final time)
    - Rule 2.4.3: Linear interpolation if exact cut-off is between time intervals
    - Rule 2.4.5: Use hot cut-off voltage for non-standard temperatures
    - Rule 2.8.1: Check against minimum duration requirement

    ALL computation happens in BigQuery SQL and Python — not in the LLM.

    Args:
        battery_code: Battery identifier (e.g. '44', '46')
        build_number: Build identifier (e.g. '108', '208')
        discharge_temperature: Filter by temperature (e.g. '+55', '-30'). If None, analyzes all.

    Returns:
        dict with computed discharge_duration_seconds, pass/fail status, and supporting data.
    """
    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    sql = f"""
        WITH discharge AS (
            SELECT
                battery_code,
                battery_name,
                build_number,
                discharge_temperature,
                discharge_type,
                cutoff_voltage_high_temp,
                cutoff_voltage_low_temp,
                target_duration_seconds,
                time_seconds,
                voltage_volts,
                discharge_current_amps,
                -- Determine which cutoff to use based on temperature (Rule 2.4.5)
                CASE
                    WHEN SAFE_CAST(REPLACE(discharge_temperature, '+', '') AS FLOAT64) >= 20
                    THEN cutoff_voltage_high_temp
                    ELSE cutoff_voltage_low_temp
                END AS applicable_cutoff_voltage
            FROM {_full_table('discharge_data')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              {temp_filter}
        ),
        -- Find the peak voltage time (voltage rising phase ends here)
        peak AS (
            SELECT
                discharge_temperature,
                discharge_type,
                MAX(voltage_volts) AS peak_voltage,
                MIN(time_seconds) AS min_time
            FROM discharge
            GROUP BY discharge_temperature, discharge_type
        ),
        -- Get the first row to extract metadata
        metadata AS (
            SELECT DISTINCT
                battery_name,
                discharge_temperature,
                discharge_type,
                cutoff_voltage_high_temp,
                cutoff_voltage_low_temp,
                target_duration_seconds,
                applicable_cutoff_voltage
            FROM discharge
        ),
        -- For the FALLING phase: find the last crossing of the cutoff voltage
        -- Rule 2.4.1: "final occurrence of the Cut-Off-Voltage in the Voltage column"
        falling_phase AS (
            SELECT
                d.discharge_temperature,
                d.discharge_type,
                d.time_seconds,
                d.voltage_volts,
                d.applicable_cutoff_voltage,
                LAG(d.time_seconds) OVER (
                    PARTITION BY d.discharge_temperature, d.discharge_type
                    ORDER BY d.time_seconds
                ) AS prev_time,
                LAG(d.voltage_volts) OVER (
                    PARTITION BY d.discharge_temperature, d.discharge_type
                    ORDER BY d.time_seconds
                ) AS prev_voltage
            FROM discharge d
            JOIN peak p ON d.discharge_temperature = p.discharge_temperature
                       AND d.discharge_type = p.discharge_type
            WHERE d.time_seconds > p.min_time  -- Only after initial phase
        ),
        -- Find the LAST time voltage crosses below cutoff (Rule 2.4.1)
        cutoff_crossings AS (
            SELECT
                discharge_temperature,
                discharge_type,
                applicable_cutoff_voltage,
                time_seconds,
                voltage_volts,
                prev_time,
                prev_voltage,
                -- This row is a crossing: prev was above cutoff, current is at or below
                ROW_NUMBER() OVER (
                    PARTITION BY discharge_temperature, discharge_type 
                    ORDER BY time_seconds DESC
                ) AS rn
            FROM falling_phase
            WHERE prev_voltage IS NOT NULL
              AND prev_voltage >= applicable_cutoff_voltage
              AND voltage_volts <= applicable_cutoff_voltage
        )
        SELECT
            m.battery_name,
            m.discharge_temperature,
            m.discharge_type,
            m.cutoff_voltage_high_temp,
            m.cutoff_voltage_low_temp,
            m.applicable_cutoff_voltage,
            m.target_duration_seconds,
            c.time_seconds AS crossing_time_after,
            c.voltage_volts AS crossing_voltage_after,
            c.prev_time AS crossing_time_before,
            c.prev_voltage AS crossing_voltage_before
        FROM metadata m
        LEFT JOIN cutoff_crossings c ON m.discharge_temperature = c.discharge_temperature
                                    AND m.discharge_type = c.discharge_type
                                    AND c.rn = 1
        ORDER BY m.discharge_temperature, m.discharge_type
    """

    try:
        client = _get_bq_client()
        result = client.query(sql).result()

        builds = []
        for row in result:
            r = dict(row.items())

            # Apply Rule 2.4.3: Linear interpolation
            discharge_duration = None
            if r.get("crossing_time_before") is not None and r.get("crossing_time_after") is not None:
                if abs(r["crossing_voltage_after"] - r["applicable_cutoff_voltage"]) < 1e-6:
                    discharge_duration = round(r["crossing_time_after"], 4)
                else:
                    discharge_duration = _interpolate_time_at_voltage(
                        r["crossing_time_before"], r["crossing_voltage_before"],
                        r["crossing_time_after"], r["crossing_voltage_after"],
                        r["applicable_cutoff_voltage"]
                    )

            # Rule 2.8.1: Check against minimum requirement
            target = r.get("target_duration_seconds")
            pass_fail = None
            if discharge_duration is not None and target is not None and target > 0:
                pass_fail = "PASS" if discharge_duration >= target else "FAIL"

            builds.append({
                "battery_name": r.get("battery_name"),
                "discharge_temperature": r.get("discharge_temperature"),
                "discharge_type": r.get("discharge_type"),
                "cutoff_voltage_used": round(r["applicable_cutoff_voltage"], 4) if r.get("applicable_cutoff_voltage") else None,
                "cutoff_voltage_high_temp": round(r["cutoff_voltage_high_temp"], 4) if r.get("cutoff_voltage_high_temp") else None,
                "cutoff_voltage_low_temp": round(r["cutoff_voltage_low_temp"], 4) if r.get("cutoff_voltage_low_temp") else None,
                "computed_discharge_duration_seconds": discharge_duration,
                "target_duration_seconds": round(target, 4) if target else None,
                "pass_fail": pass_fail,
                "interpolation_note": "Exact cut-off time computed using linear interpolation per Rule 2.4.3" if discharge_duration else "Cut-off voltage not reached in data",
            })

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (BigQuery SQL + Python interpolation)",
            "rulebook_reference": "Rule 2.4 - Discharge Duration",
            "results": builds,
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── 2. Activation Time (Rule 2.5) ──────────────────────────

def calculate_activation_time(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Calculate EXACT activation time following RESL Rulebook Rule 2.5.

    Rule 2.5.1: During the initial rising phase, find when voltage FIRST
    reaches the cut-off voltage.
    Rule 2.5.2: Use linear interpolation if between time slots.

    Args:
        battery_code: Battery identifier
        build_number: Build identifier
        discharge_temperature: Optional temperature filter

    Returns:
        dict with computed activation_time_seconds and supporting data.
    """
    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    sql = f"""
        WITH discharge AS (
            SELECT
                discharge_temperature,
                discharge_type,
                time_seconds,
                voltage_volts,
                max_activation_time_ms,
                CASE
                    WHEN SAFE_CAST(REPLACE(discharge_temperature, '+', '') AS FLOAT64) >= 20
                    THEN cutoff_voltage_high_temp
                    ELSE cutoff_voltage_low_temp
                END AS applicable_cutoff_voltage
            FROM {_full_table('discharge_data')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              {temp_filter}
        ),
        -- Find peak voltage to identify end of rising phase
        peak AS (
            SELECT
                discharge_temperature,
                discharge_type,
                MAX(voltage_volts) AS peak_voltage
            FROM discharge
            GROUP BY discharge_temperature, discharge_type
        ),
        -- Rising phase only: before peak voltage
        rising AS (
            SELECT
                d.discharge_temperature,
                d.discharge_type,
                d.time_seconds,
                d.voltage_volts,
                d.applicable_cutoff_voltage,
                d.max_activation_time_ms,
                LAG(d.time_seconds) OVER (
                    PARTITION BY d.discharge_temperature, d.discharge_type
                    ORDER BY d.time_seconds
                ) AS prev_time,
                LAG(d.voltage_volts) OVER (
                    PARTITION BY d.discharge_temperature, d.discharge_type
                    ORDER BY d.time_seconds
                ) AS prev_voltage
            FROM discharge d
            -- Only consider first few seconds (activation happens early)
            WHERE d.time_seconds <= 10
        ),
        -- Find FIRST time voltage crosses above cutoff in rising phase (Rule 2.5.1)
        first_crossing AS (
            SELECT
                discharge_temperature,
                discharge_type,
                applicable_cutoff_voltage,
                max_activation_time_ms,
                time_seconds,
                voltage_volts,
                prev_time,
                prev_voltage,
                ROW_NUMBER() OVER (
                    PARTITION BY discharge_temperature, discharge_type
                    ORDER BY time_seconds ASC
                ) AS rn
            FROM rising
            WHERE prev_voltage IS NOT NULL
              AND prev_voltage < applicable_cutoff_voltage
              AND voltage_volts >= applicable_cutoff_voltage
        )
        SELECT * FROM first_crossing WHERE rn = 1
        ORDER BY discharge_temperature, discharge_type
    """

    try:
        client = _get_bq_client()
        result = client.query(sql).result()

        activations = []
        for row in result:
            r = dict(row.items())

            # Rule 2.5.2: Linear interpolation
            if abs(r["voltage_volts"] - r["applicable_cutoff_voltage"]) < 1e-6:
                activation_time = round(r["time_seconds"], 4)
            else:
                activation_time = _interpolate_time_at_voltage(
                    r["prev_time"], r["prev_voltage"],
                    r["time_seconds"], r["voltage_volts"],
                    r["applicable_cutoff_voltage"]
                )

            max_allowed_ms = r.get("max_activation_time_ms")
            pass_fail = None
            if max_allowed_ms and max_allowed_ms > 0:
                pass_fail = "PASS" if (activation_time * 1000) <= max_allowed_ms else "FAIL"

            activations.append({
                "discharge_temperature": r.get("discharge_temperature"),
                "discharge_type": r.get("discharge_type"),
                "cutoff_voltage_used": round(r["applicable_cutoff_voltage"], 4),
                "computed_activation_time_seconds": activation_time,
                "computed_activation_time_ms": round(activation_time * 1000, 2),
                "max_allowed_activation_time_ms": round(max_allowed_ms, 2) if max_allowed_ms else None,
                "pass_fail": pass_fail,
            })

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (BigQuery SQL + Python interpolation)",
            "rulebook_reference": "Rule 2.5 - Activation Time",
            "results": activations,
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── 3. Max Open Circuit Voltage (Rule 2.6) ─────────────────

def calculate_open_circuit_voltage(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Calculate Maximum Open Circuit Voltage following Rule 2.6.

    Rule 2.6.1: From the start of discharge, go down the current column.
    The current values will be zero or close to zero. The maximum voltage
    seen before the discharge current is applied is the Max OCV.

    Args:
        battery_code: Battery identifier
        build_number: Build identifier
        discharge_temperature: Optional temperature filter

    Returns:
        dict with max_open_circuit_voltage and supporting data.
    """
    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    sql = f"""
        WITH ordered AS (
            SELECT
                discharge_temperature,
                discharge_type,
                time_seconds,
                voltage_volts,
                discharge_current_amps,
                max_open_circuit_voltage AS spec_max_ocv,
                ROW_NUMBER() OVER (
                    PARTITION BY discharge_temperature, discharge_type
                    ORDER BY time_seconds ASC
                ) AS rn
            FROM {_full_table('discharge_data')}
            WHERE battery_code = '{battery_code}'
              AND build_number = '{build_number}'
              {temp_filter}
        ),
        -- Find when current first goes significantly above zero
        current_onset AS (
            SELECT
                discharge_temperature,
                discharge_type,
                MIN(time_seconds) AS current_start_time
            FROM ordered
            WHERE ABS(discharge_current_amps) > 0.5  -- Current applied
            GROUP BY discharge_temperature, discharge_type
        ),
        -- Max voltage BEFORE current was applied (open circuit region)
        ocv AS (
            SELECT
                o.discharge_temperature,
                o.discharge_type,
                MAX(o.voltage_volts) AS measured_max_ocv,
                MAX(o.spec_max_ocv) AS spec_max_ocv,
                c.current_start_time
            FROM ordered o
            JOIN current_onset c ON o.discharge_temperature = c.discharge_temperature
                                AND o.discharge_type = c.discharge_type
            WHERE o.time_seconds < c.current_start_time
            GROUP BY o.discharge_temperature, o.discharge_type, c.current_start_time
        )
        SELECT * FROM ocv
        ORDER BY discharge_temperature, discharge_type
    """

    try:
        client = _get_bq_client()
        result = client.query(sql).result()

        results = []
        for row in result:
            r = dict(row.items())
            measured = round(r["measured_max_ocv"], 4) if r.get("measured_max_ocv") is not None else None
            spec = round(r["spec_max_ocv"], 4) if r.get("spec_max_ocv") is not None else None

            pass_fail = None
            if measured is not None and spec is not None and spec > 0:
                pass_fail = "PASS" if measured <= spec else "FAIL (exceeds spec)"

            results.append({
                "discharge_temperature": r.get("discharge_temperature"),
                "discharge_type": r.get("discharge_type"),
                "measured_max_open_circuit_voltage_V": measured,
                "spec_max_open_circuit_voltage_V": spec,
                "current_applied_at_seconds": round(r.get("current_start_time", 0), 4),
                "pass_fail": pass_fail,
            })

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (BigQuery SQL)",
            "rulebook_reference": "Rule 2.6 - Maximum Open Circuit Voltage",
            "results": results,
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── 4. Max On-Load Voltage (Rule 2.7) ──────────────────────

def calculate_on_load_voltage(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Calculate Maximum On-Load Voltage following Rule 2.7.

    Rule 2.7.1: Maximum voltage when current is being drawn from the battery.

    Args:
        battery_code: Battery identifier
        build_number: Build identifier
        discharge_temperature: Optional temperature filter

    Returns:
        dict with max_on_load_voltage and supporting data.
    """
    temp_filter = ""
    if discharge_temperature:
        temp_filter = f"AND discharge_temperature = '{discharge_temperature}'"

    sql = f"""
        SELECT
            discharge_temperature,
            discharge_type,
            ROUND(MAX(voltage_volts), 4) AS max_on_load_voltage_V,
            ROUND(AVG(discharge_current_amps), 4) AS avg_discharge_current_A,
            ROUND(MAX(discharge_current_amps), 4) AS max_discharge_current_A
        FROM {_full_table('discharge_data')}
        WHERE battery_code = '{battery_code}'
          AND build_number = '{build_number}'
          AND ABS(discharge_current_amps) > 0.5  -- Only when current is flowing
          {temp_filter}
        GROUP BY discharge_temperature, discharge_type
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
                "max_on_load_voltage_V": r.get("max_on_load_voltage_V"),
                "avg_discharge_current_A": r.get("avg_discharge_current_A"),
                "max_discharge_current_A": r.get("max_discharge_current_A"),
            })

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "Server-side (BigQuery SQL)",
            "rulebook_reference": "Rule 2.7 - Maximum On-Load Voltage",
            "results": results,
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── 5. Complete Build Analysis (combines all rules) ────────

def analyze_build_complete(
    battery_code: str,
    build_number: str,
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Complete build analysis — computes ALL rulebook metrics in one call.

    This is the primary tool for discharge analysis. It computes:
    1. Discharge Duration (Rule 2.4) with interpolation
    2. Activation Time (Rule 2.5) with interpolation
    3. Max Open Circuit Voltage (Rule 2.6)
    4. Max On-Load Voltage (Rule 2.7)
    5. Pass/Fail status (Rule 2.8)
    6. Key statistics (min/max/avg voltage, current, duration)

    ALL computation done server-side. Returns only final numerical results.

    Args:
        battery_code: Battery identifier
        build_number: Build identifier
        discharge_temperature: Optional temperature filter

    Returns:
        Complete analysis results with all computed metrics.
    """
    # Run all analyses
    duration_result = calculate_discharge_duration(battery_code, build_number, discharge_temperature)
    activation_result = calculate_activation_time(battery_code, build_number, discharge_temperature)
    ocv_result = calculate_open_circuit_voltage(battery_code, build_number, discharge_temperature)
    onload_result = calculate_on_load_voltage(battery_code, build_number, discharge_temperature)

    # Get overall statistics via BigQuery aggregation
    temp_filter = f"AND discharge_temperature = '{discharge_temperature}'" if discharge_temperature else ""

    stats_sql = f"""
        SELECT
            discharge_temperature,
            discharge_type,
            COUNT(*) AS total_data_points,
            ROUND(MIN(time_seconds), 4) AS start_time_s,
            ROUND(MAX(time_seconds), 4) AS end_time_s,
            ROUND(MIN(voltage_volts), 4) AS min_voltage_V,
            ROUND(MAX(voltage_volts), 4) AS max_voltage_V,
            ROUND(AVG(voltage_volts), 4) AS avg_voltage_V,
            ROUND(MIN(discharge_current_amps), 4) AS min_current_A,
            ROUND(MAX(discharge_current_amps), 4) AS max_current_A,
            ROUND(AVG(discharge_current_amps), 4) AS avg_current_A,
            MAX(target_duration_seconds) AS target_duration_s,
            MAX(cutoff_voltage_high_temp) AS cutoff_high_V,
            MAX(cutoff_voltage_low_temp) AS cutoff_low_V
        FROM {_full_table('discharge_data')}
        WHERE battery_code = '{battery_code}'
          AND build_number = '{build_number}'
          {temp_filter}
        GROUP BY discharge_temperature, discharge_type
        ORDER BY discharge_temperature, discharge_type
    """

    try:
        client = _get_bq_client()
        stats_result = client.query(stats_sql).result()
        stats_data = []
        for row in stats_result:
            stats_data.append({k: (round(v, 4) if isinstance(v, float) else v) for k, v in dict(row.items()).items()})

        return {
            "status": "success",
            "battery_code": battery_code,
            "build_number": build_number,
            "computation": "ALL metrics computed server-side (BigQuery + Python)",
            "note": "Every number below is computed from ALL data points. No sampling, no estimation.",
            "statistics": stats_data,
            "discharge_duration": duration_result.get("results", []),
            "activation_time": activation_result.get("results", []),
            "open_circuit_voltage": ocv_result.get("results", []),
            "on_load_voltage": onload_result.get("results", []),
        }

    except Exception as e:
        return {"status": "error", "error_message": str(e)}


# ── 6. Multi-Build Comparison ──────────────────────────────

def compare_builds_performance(
    battery_code: str,
    build_numbers: list[str],
    discharge_temperature: Optional[str] = None,
) -> dict:
    """Compare discharge performance across multiple builds.

    For each build, computes discharge duration and activation time,
    then provides a comparison table.

    Args:
        battery_code: Battery identifier
        build_numbers: List of builds to compare (e.g. ['108', '109', '110'])
        discharge_temperature: Optional temperature filter

    Returns:
        Comparative analysis across builds with per-build metrics.
    """
    comparison = []

    for bn in build_numbers[:10]:  # Limit to 10 builds max
        result = analyze_build_complete(battery_code, bn, discharge_temperature)
        if result.get("status") == "success":
            # Extract first result for each metric
            dur = result.get("discharge_duration", [{}])
            act = result.get("activation_time", [{}])
            ocv = result.get("open_circuit_voltage", [{}])
            onl = result.get("on_load_voltage", [{}])
            stats = result.get("statistics", [{}])

            build_summary = {
                "build_number": bn,
                "discharge_temperature": dur[0].get("discharge_temperature") if dur else None,
                "discharge_type": dur[0].get("discharge_type") if dur else None,
                "discharge_duration_s": dur[0].get("computed_discharge_duration_seconds") if dur else None,
                "target_duration_s": dur[0].get("target_duration_seconds") if dur else None,
                "duration_pass_fail": dur[0].get("pass_fail") if dur else None,
                "activation_time_ms": act[0].get("computed_activation_time_ms") if act else None,
                "activation_pass_fail": act[0].get("pass_fail") if act else None,
                "max_ocv_V": ocv[0].get("measured_max_open_circuit_voltage_V") if ocv else None,
                "max_on_load_V": onl[0].get("max_on_load_voltage_V") if onl else None,
                "avg_voltage_V": stats[0].get("avg_voltage_V") if stats else None,
                "total_data_points": stats[0].get("total_data_points") if stats else None,
            }
            comparison.append(build_summary)

    return {
        "status": "success",
        "battery_code": battery_code,
        "builds_compared": len(comparison),
        "computation": "ALL metrics computed server-side per rulebook procedures",
        "comparison": comparison,
    }
