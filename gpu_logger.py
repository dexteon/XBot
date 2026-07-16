"""GPU Logger — logs per-process GPU usage to CSV for historical tracking."""

import subprocess
import csv
import time
import sys
import os
from datetime import datetime
from pathlib import Path
import threading

LOG_DIR = Path.home() / ".xbot" / "gpu_logs"
LOG_FILE = LOG_DIR / f"gpu_{datetime.now().strftime('%Y-%m-%d')}.csv"
SAMPLE_INTERVAL = 5  # seconds between samples


def query_gpu() -> dict:
    """Query nvidia-smi for GPU stats."""
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,fan.speed,clocks.sm,clocks.mem",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}

        values = [v.strip() for v in result.stdout.strip().split(",")]
        return {
            "timestamp": datetime.now().isoformat(),
            "temp_c": float(values[0]),
            "gpu_util_pct": float(values[1]),
            "mem_util_pct": float(values[2]),
            "vram_used_mb": int(float(values[3])),
            "vram_total_mb": int(float(values[4])),
            "power_draw_w": float(values[5]),
            "power_limit_w": float(values[6]),
            "fan_speed_pct": float(values[7]),
            "sm_clock_mhz": int(float(values[8])),
            "mem_clock_mhz": int(float(values[9])),
        }
    except (subprocess.TimeoutExpired, ValueError, IndexError) as e:
        return {}


def query_processes() -> list[dict]:
    """Get per-process GPU usage from nvidia-smi."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        procs = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) >= 3:
                pid = parts[0].strip()
                name = parts[1].strip()
                mem = parts[2].strip()
                # Truncate long paths
                if "\\" in name:
                    name = name.split("\\")[-1]
                procs.append({
                    "pid": pid,
                    "process": name,
                    "gpu_memory": mem,
                })
        return procs
    except (subprocess.TimeoutExpired, ValueError):
        return []


def get_process_short_name(name: str) -> str:
    """Short name for a process."""
    if "\\" in name:
        name = name.split("\\")[-1]
    return name.strip()


def write_sample(csv_writer, gpu_stats: dict, processes: list[dict]):
    """Write one sample row per process + a summary row."""
    ts = datetime.now().isoformat()

    # Summary row (GPU total)
    csv_writer.writerow({
        "timestamp": ts,
        "type": "GPU_TOTAL",
        "pid": "",
        "process": "",
        "gpu_util_pct": gpu_stats.get("gpu_util_pct", ""),
        "vram_used_mb": gpu_stats.get("vram_used_mb", ""),
        "vram_total_mb": gpu_stats.get("vram_total_mb", ""),
        "power_w": gpu_stats.get("power_draw_w", ""),
        "temp_c": gpu_stats.get("temp_c", ""),
        "fan_pct": gpu_stats.get("fan_speed_pct", ""),
    })

    # Per-process rows
    for proc in processes:
        csv_writer.writerow({
            "timestamp": ts,
            "type": "PROCESS",
            "pid": proc["pid"],
            "process": proc["process"],
            "gpu_util_pct": "",
            "vram_used_mb": proc["gpu_memory"],
            "vram_total_mb": "",
            "power_w": "",
            "temp_c": "",
            "fan_pct": "",
        })


def run_logger(interval: int = SAMPLE_INTERVAL, duration: int = 0):
    """Run the GPU logger.

    Args:
        interval: seconds between samples
        duration: total seconds to run (0 = forever)
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "timestamp", "type", "pid", "process",
        "gpu_util_pct", "vram_used_mb", "vram_total_mb",
        "power_w", "temp_c", "fan_pct",
    ]

    is_new = not LOG_FILE.exists()

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            writer.writeheader()

        elapsed = 0
        while True:
            gpu = query_gpu()
            procs = query_processes()

            if gpu:
                write_sample(writer, gpu, procs)
                f.flush()

                # Console output
                ts = datetime.now().strftime("%H:%M:%S")
                proc_summary = ", ".join(
                    f"{p['process']}({p['gpu_memory']})" for p in procs[:5]
                )
                print(
                    f"[{ts}] GPU: {gpu.get('gpu_util_pct', '?')}% | "
                    f"VRAM: {gpu.get('vram_used_mb', '?')}/{gpu.get('vram_total_mb', '?')}MB | "
                    f"Power: {gpu.get('power_draw_w', '?')}W | "
                    f"Temp: {gpu.get('temp_c', '?')}C | "
                    f"Procs: {proc_summary or 'none'}"
                )

            if duration > 0 and elapsed >= duration:
                break

            time.sleep(interval)
            elapsed += interval


def print_summary():
    """Print a summary of today's GPU log."""
    if not LOG_FILE.exists():
        print("No GPU log found for today.")
        return

    print(f"Log file: {LOG_FILE}")
    print(f"Size: {LOG_FILE.stat().st_size / 1024:.1f} KB")
    print()

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("No data in log.")
        return

    # Filter GPU_TOTAL rows
    totals = [r for r in rows if r["type"] == "GPU_TOTAL"]
    if not totals:
        print("No GPU total samples.")
        return

    utils = [float(r["gpu_util_pct"]) for r in totals if r["gpu_util_pct"]]
    vrams = [int(r["vram_used_mb"]) for r in totals if r["vram_used_mb"]]
    powers = [float(r["power_w"]) for r in totals if r["power_w"]]
    temps = [float(r["temp_c"]) for r in totals if r["temp_c"]]

    print(f"Samples: {len(totals)}")
    print(f"Time range: {totals[0]['timestamp']} → {totals[-1]['timestamp']}")
    print()
    print(f"{'Metric':<20} {'Min':<10} {'Avg':<10} {'Max':<10}")
    print(f"{'─'*50}")

    if utils:
        print(f"{'GPU Util %':<20} {min(utils):<10.1f} {sum(utils)/len(utils):<10.1f} {max(utils):<10.1f}")
    if vrams:
        print(f"{'VRAM Used MB':<20} {min(vrams):<10d} {sum(vrams)//len(vrams):<10d} {max(vrams):<10d}")
    if powers:
        print(f"{'Power W':<20} {min(powers):<10.1f} {sum(powers)/len(powers):<10.1f} {max(powers):<10.1f}")
    if temps:
        print(f"{'Temp C':<20} {min(temps):<10.1f} {sum(temps)/len(temps):<10.1f} {max(temps):<10.1f}")

    # Process summary
    proc_rows = [r for r in rows if r["type"] == "PROCESS"]
    proc_names = {}
    for r in proc_rows:
        name = r["process"]
        if name not in proc_names:
            proc_names[name] = 0
        proc_names[name] += 1

    print()
    print(f"{'Process':<35} {'Samples':<10}")
    print(f"{'─'*45}")
    for name, count in sorted(proc_names.items(), key=lambda x: -x[1]):
        print(f"{name:<35} {count:<10}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GPU Logger — track GPU usage to CSV")
    parser.add_argument("--interval", type=int, default=SAMPLE_INTERVAL,
                        help=f"Seconds between samples (default: {SAMPLE_INTERVAL})")
    parser.add_argument("--duration", type=int, default=0,
                        help="Total seconds to run (default: 0 = forever)")
    parser.add_argument("--summary", action="store_true",
                        help="Print summary of today's log and exit")
    args = parser.parse_args()

    if args.summary:
        print_summary()
    else:
        print(f"GPU Logger starting — interval: {args.interval}s, duration: {args.duration or 'forever'}s")
        print(f"Logging to: {LOG_FILE}")
        print("Press Ctrl+C to stop\n")
        try:
            run_logger(interval=args.interval, duration=args.duration)
        except KeyboardInterrupt:
            print("\nStopped.")
            print_summary()


if __name__ == "__main__":
    main()