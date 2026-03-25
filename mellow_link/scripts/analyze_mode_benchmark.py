"""
Mode Distribution Benchmark Analysis Helper

Optional helper script to analyze benchmark results or query metrics from DB.

Usage (from project root or from mellow_link):
    # Analyze existing report (relative path is under mellow_link/outputs/bench/)
    python mellow_link/scripts/analyze_mode_benchmark.py --report outputs/bench/mode_benchmark_20240218_120000.json
    # Or from mellow_link dir: python scripts/analyze_mode_benchmark.py --report outputs/bench/mode_benchmark_20240218_120000.json

    # Query metrics from DB for a time range
    python mellow_link/scripts/analyze_mode_benchmark.py --query-db --start-time "2024-02-18 12:00:00" --end-time "2024-02-18 12:30:00"
"""

import json
import sys
import argparse
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
import statistics

# 프로젝트 루트를 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 스크립트 기준 mellow_link 디렉터리 (상대 경로 --report 해석용)
MELLOW_LINK_DIR = Path(__file__).resolve().parent.parent


def load_report(report_path: Path) -> Dict[str, Any]:
    """리포트 파일 로드"""
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def print_report_analysis(report: Dict[str, Any]) -> None:
    """리포트 분석 출력"""
    print("\n" + "=" * 80)
    print("Benchmark Report Analysis")
    print("=" * 80)
    print(f"Timestamp: {report.get('timestamp')}")
    print(f"Total Requests: {report.get('total_requests')}")
    print(f"Successful: {report.get('successful_requests')}")
    print(f"Failed: {report.get('failed_requests')}")

    print("\n--- Mode Distribution ---")
    mode_counts = report.get("mode_counts", {})
    successful = report.get("successful_requests", 0)
    for mode, count in mode_counts.items():
        percent = (count / successful * 100) if successful > 0 else 0
        print(f"  {mode}: {count} ({percent:.1f}%)")

    print("\n--- Fallback Statistics ---")
    print(f"  Triggered: {report.get('fallback_triggered_count', 0)}")
    print(f"  Blocked: {report.get('fallback_blocked_count', 0)}")

    print("\n--- Performance Metrics ---")
    infer_stats = report.get("infer_ms_stats", {})
    if infer_stats.get("p50") is not None:
        print(f"  INFER_MS:")
        print(f"    p50: {infer_stats['p50']:.1f}ms")
        print(f"    p95: {infer_stats['p95']:.1f}ms")
        print(f"    mean: {infer_stats['mean']:.1f}ms")
        print(f"    min: {infer_stats['min']:.1f}ms")
        print(f"    max: {infer_stats['max']:.1f}ms")

    ttft_stats = report.get("ttft_ms_stats", {})
    if ttft_stats.get("p50") is not None:
        print(f"  TTFT_MS (measured only):")
        print(f"    p50: {ttft_stats['p50']:.1f}ms")
        print(f"    p95: {ttft_stats['p95']:.1f}ms")
        print(f"    mean: {ttft_stats['mean']:.1f}ms")

    tps_stats = report.get("tps_stats", {})
    if tps_stats.get("p50") is not None:
        print(f"  TPS:")
        print(f"    p50: {tps_stats['p50']:.2f} tokens/s")
        print(f"    p95: {tps_stats['p95']:.2f} tokens/s")
        print(f"    mean: {tps_stats['mean']:.2f} tokens/s")

    if report.get("warnings"):
        print("\n--- Warnings ---")
        for warning in report["warnings"]:
            print(f"  ⚠️  {warning}")
    else:
        print("\n--- No Warnings ---")

    if report.get("outliers"):
        print("\n--- Top 5 INFER_MS Outliers ---")
        for i, outlier in enumerate(report["outliers"], 1):
            print(f"  {i}. Index {outlier['index']}: {outlier['infer_ms']:.1f}ms - {outlier['prompt']}")

    print("=" * 80)


def query_db_metrics(start_time: str, end_time: str) -> None:
    """DB에서 메트릭 조회"""
    # DB 경로 찾기 (여러 가능한 위치 확인)
    possible_paths = [
        Path(__file__).parent.parent / "data" / "mellow_link_memory.db",
        Path(__file__).parent.parent.parent / "mellow_link" / "data" / "mellow_link_memory.db",
        PROJECT_ROOT / "mellow_link" / "data" / "mellow_link_memory.db",
    ]

    db_path = None
    for path in possible_paths:
        if path.exists():
            db_path = path
            break

    if not db_path:
        print(f"❌ Memory database not found. Checked paths:")
        for path in possible_paths:
            print(f"  - {path}")
        return

    try:
        start_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp()
    except ValueError as e:
        print(f"❌ Invalid time format. Use: YYYY-MM-DD HH:MM:SS")
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        print(f"\nQuerying metrics from {start_time} to {end_time}...")

        cursor.execute("""
            SELECT category, value, unit, timestamp
            FROM performance_metrics
            WHERE timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        """, (start_ts, end_ts))

        rows = cursor.fetchall()
        if not rows:
            print("No metrics found in the specified time range.")
            return

        # 카테고리별로 그룹화
        metrics_by_category: Dict[str, List[float]] = {}
        for row in rows:
            category = row["category"]
            value = row["value"]
            if category not in metrics_by_category:
                metrics_by_category[category] = []
            metrics_by_category[category].append(value)

        print(f"\nFound {len(rows)} metric records:")
        print("=" * 80)

        for category, values in sorted(metrics_by_category.items()):
            if not values:
                continue
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            print(f"\n{category}:")
            print(f"  Count: {n}")
            print(f"  Min: {min(sorted_vals):.2f}")
            print(f"  Max: {max(sorted_vals):.2f}")
            print(f"  Mean: {statistics.mean(sorted_vals):.2f}")
            print(f"  p50: {sorted_vals[n // 2]:.2f}")
            if n > 1:
                print(f"  p95: {sorted_vals[int(n * 0.95)]:.2f}")

        print("=" * 80)

    finally:
        conn.close()


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description="Mode Distribution Benchmark Analysis Helper")
    parser.add_argument(
        "--report",
        type=str,
        help="Path to benchmark report JSON file",
    )
    parser.add_argument(
        "--query-db",
        action="store_true",
        help="Query metrics from database",
    )
    parser.add_argument(
        "--start-time",
        type=str,
        help="Start time for DB query (format: YYYY-MM-DD HH:MM:SS)",
    )
    parser.add_argument(
        "--end-time",
        type=str,
        help="End time for DB query (format: YYYY-MM-DD HH:MM:SS)",
    )

    args = parser.parse_args()

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = (MELLOW_LINK_DIR / report_path).resolve()
        if not report_path.exists():
            print(f"❌ Report file not found: {report_path}")
            return
        report = load_report(report_path)
        print_report_analysis(report)

    elif args.query_db:
        if not args.start_time or not args.end_time:
            print("❌ --start-time and --end-time are required for --query-db")
            return
        query_db_metrics(args.start_time, args.end_time)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
