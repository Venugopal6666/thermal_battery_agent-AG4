"""Physics and electrochemistry calculation tools for thermal battery analysis."""

from __future__ import annotations
from typing import Optional
import math


def calculate_specific_energy(voltage_v: float, capacity_ah: float, mass_kg: float) -> dict:
    """Calculate the specific energy of a battery (energy per unit mass).

    Args:
        voltage_v: Average operating voltage in Volts.
        capacity_ah: Battery capacity in Ampere-hours.
        mass_kg: Battery mass in kilograms.

    Returns:
        dict with specific energy in Wh/kg.
    """
    if mass_kg <= 0:
        return {"status": "error", "error_message": "Mass must be positive."}
    energy_wh = voltage_v * capacity_ah
    specific_energy = energy_wh / mass_kg
    return {
        "status": "success",
        "energy_wh": round(energy_wh, 4),
        "specific_energy_wh_per_kg": round(specific_energy, 4),
    }


def calculate_energy_density(voltage_v: float, capacity_ah: float, volume_liters: float) -> dict:
    """Calculate the energy density of a battery (energy per unit volume).

    Args:
        voltage_v: Average operating voltage in Volts.
        capacity_ah: Battery capacity in Ampere-hours.
        volume_liters: Battery volume in liters.

    Returns:
        dict with energy density in Wh/L.
    """
    if volume_liters <= 0:
        return {"status": "error", "error_message": "Volume must be positive."}
    energy_wh = voltage_v * capacity_ah
    energy_density = energy_wh / volume_liters
    return {
        "status": "success",
        "energy_wh": round(energy_wh, 4),
        "energy_density_wh_per_l": round(energy_density, 4),
    }


def calculate_c_rate(current_a: float, capacity_ah: float) -> dict:
    """Calculate the C-rate (charge/discharge rate relative to capacity).

    Args:
        current_a: Discharge or charge current in Amperes.
        capacity_ah: Battery nominal capacity in Ampere-hours.

    Returns:
        dict with C-rate value and estimated discharge time.
    """
    if capacity_ah <= 0:
        return {"status": "error", "error_message": "Capacity must be positive."}
    c_rate = current_a / capacity_ah
    discharge_time_hours = 1 / c_rate if c_rate > 0 else float("inf")
    return {
        "status": "success",
        "c_rate": round(c_rate, 4),
        "discharge_time_hours": round(discharge_time_hours, 4),
        "discharge_time_minutes": round(discharge_time_hours * 60, 2),
    }


def analyze_discharge_curve(
    time_seconds: list[float],
    voltage_volts: list[float],
    current_amps: Optional[list[float]] = None,
) -> dict:
    """Analyze a discharge curve from time-series data.

    Calculates key metrics: average voltage, voltage drop rate, plateau detection,
    activation time, and total discharge duration.

    Args:
        time_seconds: List of time points in seconds.
        voltage_volts: List of voltage readings in Volts at each time point.
        current_amps: Optional list of current readings in Amps at each time point.

    Returns:
        dict with analysis results including avg voltage, min/max voltage,
        voltage drop rate, total duration, and capacity estimate.
    """
    if not time_seconds or not voltage_volts:
        return {"status": "error", "error_message": "Empty data provided."}
    if len(time_seconds) != len(voltage_volts):
        return {"status": "error", "error_message": "time and voltage arrays must have same length."}

    n = len(voltage_volts)
    avg_voltage = sum(voltage_volts) / n
    min_voltage = min(voltage_volts)
    max_voltage = max(voltage_volts)
    total_duration = max(time_seconds) - min(time_seconds) if n > 1 else 0

    # Voltage drop rate (V/s)
    voltage_drop_rate = (voltage_volts[0] - voltage_volts[-1]) / total_duration if total_duration > 0 else 0

    # Find activation time (time to reach peak voltage)
    peak_idx = voltage_volts.index(max_voltage)
    activation_time = time_seconds[peak_idx] - time_seconds[0]

    result = {
        "status": "success",
        "data_points": n,
        "total_duration_seconds": round(total_duration, 3),
        "average_voltage_v": round(avg_voltage, 4),
        "min_voltage_v": round(min_voltage, 4),
        "max_voltage_v": round(max_voltage, 4),
        "voltage_drop_rate_v_per_s": round(voltage_drop_rate, 6),
        "activation_time_seconds": round(activation_time, 3),
    }

    # Estimate capacity if current data is provided
    if current_amps and len(current_amps) == n and n > 1:
        # Trapezoidal integration of current over time (A·s → A·h)
        charge_as = 0
        for i in range(1, n):
            dt = time_seconds[i] - time_seconds[i - 1]
            avg_current = (abs(current_amps[i]) + abs(current_amps[i - 1])) / 2
            charge_as += avg_current * dt
        capacity_ah = charge_as / 3600
        avg_current_a = sum(abs(c) for c in current_amps) / n
        energy_wh = capacity_ah * avg_voltage

        result["estimated_capacity_ah"] = round(capacity_ah, 6)
        result["average_current_a"] = round(avg_current_a, 4)
        result["estimated_energy_wh"] = round(energy_wh, 6)

    return result


def calculate_thermal_efficiency(heat_input_j: float, useful_output_j: float) -> dict:
    """Calculate thermal efficiency of the battery system.

    Args:
        heat_input_j: Total heat input energy in Joules.
        useful_output_j: Useful electrical output energy in Joules.

    Returns:
        dict with thermal efficiency percentage and heat loss.
    """
    if heat_input_j <= 0:
        return {"status": "error", "error_message": "Heat input must be positive."}
    efficiency = (useful_output_j / heat_input_j) * 100
    heat_loss_j = heat_input_j - useful_output_j
    return {
        "status": "success",
        "thermal_efficiency_percent": round(efficiency, 2),
        "heat_loss_joules": round(heat_loss_j, 4),
        "useful_output_joules": round(useful_output_j, 4),
    }


def analyze_temperature_profile(
    time_seconds: list[float],
    t1: list[float],
    t2: list[float],
    t3: list[float],
) -> dict:
    """Analyze temperature sensor data from a battery test.

    Args:
        time_seconds: List of time points in seconds.
        t1: Sensor 1 temperature readings in °C.
        t2: Sensor 2 temperature readings in °C.
        t3: Sensor 3 temperature readings in °C.

    Returns:
        dict with per-sensor stats (min, max, avg, peak time) and uniformity metrics.
    """
    if not time_seconds or not t1 or not t2 or not t3:
        return {"status": "error", "error_message": "Empty data provided."}

    def sensor_stats(temps, name):
        avg_t = sum(temps) / len(temps)
        max_t = max(temps)
        min_t = min(temps)
        peak_idx = temps.index(max_t)
        peak_time = time_seconds[peak_idx]
        heating_rate = (max_t - temps[0]) / peak_time if peak_time > 0 else 0
        return {
            f"{name}_avg_c": round(avg_t, 2),
            f"{name}_max_c": round(max_t, 2),
            f"{name}_min_c": round(min_t, 2),
            f"{name}_peak_time_s": round(peak_time, 3),
            f"{name}_heating_rate_c_per_s": round(heating_rate, 4),
        }

    result = {"status": "success", "data_points": len(time_seconds)}
    result.update(sensor_stats(t1, "t1"))
    result.update(sensor_stats(t2, "t2"))
    result.update(sensor_stats(t3, "t3"))

    # Temperature uniformity — max difference between sensors at any time point
    max_spread = 0
    for i in range(len(time_seconds)):
        temps = [t1[i], t2[i], t3[i]]
        spread = max(temps) - min(temps)
        if spread > max_spread:
            max_spread = spread

    result["max_temperature_spread_c"] = round(max_spread, 2)

    return result


def calculate_internal_resistance(
    ocv_voltage: float, load_voltage: float, load_current_a: float
) -> dict:
    """Calculate the internal resistance of the battery.

    Args:
        ocv_voltage: Open circuit voltage in Volts.
        load_voltage: Voltage under load in Volts.
        load_current_a: Load current in Amperes.

    Returns:
        dict with internal resistance in Ohms and milliOhms.
    """
    if load_current_a <= 0:
        return {"status": "error", "error_message": "Load current must be positive."}
    voltage_drop = ocv_voltage - load_voltage
    resistance_ohms = voltage_drop / load_current_a
    return {
        "status": "success",
        "internal_resistance_ohms": round(resistance_ohms, 6),
        "internal_resistance_mohms": round(resistance_ohms * 1000, 3),
        "voltage_drop_v": round(voltage_drop, 4),
    }
