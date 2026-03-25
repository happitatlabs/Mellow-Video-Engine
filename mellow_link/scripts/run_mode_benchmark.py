"""
Mode Distribution Benchmark Runner for Mellow-Link

Automated benchmark that sends 30 prompts through /chat/ask (SSE) in mode="auto"
and captures response metadata to compute aggregates.

Usage (from project root or from mellow_link):
    python mellow_link/scripts/run_mode_benchmark.py [--api-url http://localhost:8000] [--auth-token TOKEN]
    Or from mellow_link dir: python scripts/run_mode_benchmark.py [...]
"""

import asyncio
import aiohttp
import json
import sys
import time
import statistics
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
import argparse

# 프로젝트 루트를 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 30개 프롬프트 정의 (10개 빠른 응답, 10개 도구 필요, 10개 깊은 사고)
PROMPTS = {
    "fast": [
        "안녕하세요",
        "오늘 날씨 어때요?",
        "간단히 자기소개 해주세요",
        "1+1은 뭐예요?",
        "좋아하는 색깔은?",
        "인사말 해주세요",
        "지금 몇 시예요?",
        "간단한 농담 하나 해주세요",
        "좋은 아침이에요",
        "고마워요",
    ],
    "tool": [
        "현재 시간을 알려주세요",
        "파일 시스템에서 최근 수정된 파일 목록을 보여주세요",
        "시스템 메모리 사용량을 확인해주세요",
        "현재 디렉토리의 파일 목록을 출력해주세요",
        "환경 변수 PATH를 확인해주세요",
        "현재 작업 디렉토리를 알려주세요",
        "시스템 정보를 조회해주세요",
        "네트워크 연결 상태를 확인해주세요",
        "디스크 사용량을 확인해주세요",
        "실행 중인 프로세스 목록을 보여주세요",
    ],
    "thinking": [
        "인공지능의 미래에 대해 깊이 있게 분석해주세요",
        "양자 컴퓨팅이 암호학에 미치는 영향을 설명해주세요",
        "의식의 본질에 대한 철학적 관점을 제시해주세요",
        "블록체인 기술의 장단점을 상세히 비교 분석해주세요",
        "기후 변화 해결을 위한 기술적 접근 방식을 논의해주세요",
        "인간의 창의성과 AI의 창의성 차이를 설명해주세요",
        "윤리적 AI 개발의 원칙들을 정리해주세요",
        "시간 여행의 물리학적 가능성을 탐구해주세요",
        "다중 우주 이론의 과학적 근거를 설명해주세요",
        "인간의 자유의지와 결정론의 관계를 분석해주세요",
    ],
}


@dataclass
class RequestResult:
    """단일 요청 결과"""
    index: int
    prompt: str
    prompt_category: str
    timestamp: float
    session_id: Optional[str] = None
    message_id: Optional[int] = None
    selected_mode: Optional[str] = None
    auto_selected: Optional[bool] = None
    processing_time: Optional[float] = None
    rag_used: Optional[bool] = None
    # 메트릭
    ttft_ms: Optional[float] = None
    ttft_measured: Optional[bool] = None
    tps: Optional[float] = None
    tps_approx: Optional[float] = None
    infer_ms: Optional[float] = None
    fast_fallback_triggered: Optional[bool] = None
    fast_fallback_blocked: Optional[bool] = None
    error: Optional[str] = None


@dataclass
class BenchmarkReport:
    """벤치마크 리포트"""
    timestamp: str
    total_requests: int
    successful_requests: int
    failed_requests: int
    requests: List[Dict[str, Any]]
    # 집계 통계
    mode_counts: Dict[str, int]
    fallback_triggered_count: int
    fallback_blocked_count: int
    # 메트릭 통계
    infer_ms_stats: Dict[str, Optional[float]]
    ttft_ms_stats: Dict[str, Optional[float]]
    tps_stats: Dict[str, Optional[float]]
    tps_approx_stats: Dict[str, Optional[float]]
    # 이상치
    outliers: List[Dict[str, Any]]
    # 경고
    warnings: List[str]


class BenchmarkRunner:
    """벤치마크 러너"""

    def __init__(self, api_url: str = "http://localhost:8000", auth_token: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.auth_token = auth_token
        self.session_id: Optional[str] = None
        self.results: List[RequestResult] = []

    async def send_request(
        self,
        index: int,
        prompt: str,
        prompt_category: str,
        timeout: float = 60.0,
    ) -> RequestResult:
        """단일 요청 전송 및 결과 수집"""
        start_time = time.time()
        result = RequestResult(
            index=index,
            prompt=prompt,
            prompt_category=prompt_category,
            timestamp=start_time,
        )

        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        payload = {
            "question": prompt,
            "mode": "auto",
            "session_id": self.session_id,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/chat/ask",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        result.error = f"HTTP {resp.status}: {error_text[:200]}"
                        return result

                    # SSE 스트리밍 처리
                    done_metadata = None
                    async for line in resp.content:
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if not line_str or not line_str.startswith("data: "):
                            continue

                        try:
                            data_str = line_str[6:]  # "data: " 제거
                            data = json.loads(data_str)

                            if data.get("done"):
                                done_metadata = data
                                break
                        except json.JSONDecodeError:
                            continue

                    if done_metadata:
                        result.session_id = done_metadata.get("session_id")
                        result.message_id = done_metadata.get("message_id")
                        result.processing_time = done_metadata.get("processing_time")
                        result.rag_used = done_metadata.get("rag_used", False)

                        # 다음 요청을 위해 session_id 업데이트
                        if result.session_id:
                            self.session_id = result.session_id

        except asyncio.TimeoutError:
            result.error = f"Timeout after {timeout}s"
        except aiohttp.ClientError as e:
            result.error = f"Network error: {str(e)}"
            # 네트워크 오류 시 1회 재시도
            try:
                await asyncio.sleep(1)
                return await self.send_request(index, prompt, prompt_category, timeout)
            except Exception:
                pass
        except Exception as e:
            result.error = f"Unexpected error: {str(e)}"

        return result

    async def enrich_with_db_metrics(self, result: RequestResult) -> None:
        """DB에서 추가 메트릭 조회"""
        if not result.message_id or not result.session_id:
            return

        try:
            from mellow_link.infra.database import SessionLocal, ChatMessage
            from mellow_link.infra.memory_database import get_memory_db

            # ChatMessage에서 selected_mode, auto_selected 조회
            db = SessionLocal()
            try:
                msg = db.query(ChatMessage).filter(ChatMessage.id == result.message_id).first()
                if msg:
                    result.selected_mode = msg.selected_mode
                    result.auto_selected = msg.auto_selected
            finally:
                db.close()

            # performance_metrics에서 메트릭 조회
            memory_db = get_memory_db()
            if memory_db:
                # message_id를 request_id로 사용하여 메트릭 조회
                # 또는 시간 윈도우로 조회
                request_time = datetime.fromtimestamp(result.timestamp)
                time_window_start = request_time.timestamp() - 5  # 5초 전
                time_window_end = request_time.timestamp() + 10  # 10초 후

                # performance_metrics 테이블에서 조회
                import sqlite3
                
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

                if db_path:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()

                    try:
                        # 시간 윈도우로 메트릭 조회
                        cursor.execute("""
                            SELECT category, value, unit
                            FROM performance_metrics
                            WHERE timestamp >= ? AND timestamp <= ?
                            ORDER BY timestamp ASC
                        """, (time_window_start, time_window_end))

                        rows = cursor.fetchall()
                        for row in rows:
                            category = row["category"]
                            value = row["value"]

                            if category == "TTFT_MS":
                                result.ttft_ms = value
                            elif category == "TTFT_MEASURED":
                                result.ttft_measured = bool(value)
                            elif category == "TPS":
                                result.tps = value
                            elif category == "TPS_APPROX":
                                result.tps_approx = value
                            elif category == "INFER_MS":
                                result.infer_ms = value
                            elif category == "FAST_FALLBACK_TRIGGERED":
                                result.fast_fallback_triggered = bool(value)
                            elif category == "FAST_FALLBACK_BLOCKED":
                                result.fast_fallback_blocked = bool(value)
                    finally:
                        conn.close()

        except Exception as e:
            print(f"[Warning] Failed to enrich metrics from DB: {e}")

    def compute_statistics(self, results: List[RequestResult]) -> BenchmarkReport:
        """통계 계산 및 리포트 생성"""
        successful = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]

        # 모드 카운트
        mode_counts = {"fast": 0, "thinking": 0, "unknown": 0}
        for r in successful:
            if r.selected_mode:
                mode_counts[r.selected_mode] = mode_counts.get(r.selected_mode, 0) + 1
            else:
                mode_counts["unknown"] += 1

        # Fallback 카운트
        fallback_triggered = sum(
            1 for r in successful if r.fast_fallback_triggered is True
        )
        fallback_blocked = sum(
            1 for r in successful if r.fast_fallback_blocked is True
        )

        # 메트릭 통계 계산
        def compute_stats(values: List[float], name: str) -> Dict[str, Optional[float]]:
            if not values:
                return {"p50": None, "p95": None, "mean": None, "min": None, "max": None}
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            return {
                "p50": sorted_vals[n // 2] if n > 0 else None,
                "p95": sorted_vals[int(n * 0.95)] if n > 1 else sorted_vals[-1] if n > 0 else None,
                "mean": statistics.mean(sorted_vals),
                "min": min(sorted_vals),
                "max": max(sorted_vals),
            }

        infer_ms_values = [r.infer_ms for r in successful if r.infer_ms is not None]
        infer_ms_stats = compute_stats(infer_ms_values, "INFER_MS")

        ttft_ms_values = [
            r.ttft_ms for r in successful if r.ttft_measured and r.ttft_ms is not None
        ]
        ttft_ms_stats = compute_stats(ttft_ms_values, "TTFT_MS")

        tps_values = [r.tps for r in successful if r.tps is not None]
        tps_stats = compute_stats(tps_values, "TPS")

        tps_approx_values = [r.tps_approx for r in successful if r.tps_approx is not None]
        tps_approx_stats = compute_stats(tps_approx_values, "TPS_APPROX")

        # 이상치 (INFER_MS 상위 5개)
        infer_ms_with_index = [
            (i, r.infer_ms) for i, r in enumerate(successful) if r.infer_ms is not None
        ]
        infer_ms_with_index.sort(key=lambda x: x[1], reverse=True)
        outliers = [
            {
                "index": successful[idx].index,
                "prompt": successful[idx].prompt[:50],
                "infer_ms": ms,
            }
            for idx, ms in infer_ms_with_index[:5]
        ]

        # 경고 생성
        warnings = []
        fast_count = mode_counts.get("fast", 0)
        fast_percent = (fast_count / len(successful) * 100) if successful else 0
        if fast_percent < 20:
            warnings.append(f"Fast mode usage too low: {fast_percent:.1f}% (expected >= 20%)")

        if fallback_triggered >= 5:
            warnings.append(f"Fast fallback triggered too many times: {fallback_triggered} (expected < 5)")

        if infer_ms_stats["p95"] is not None and infer_ms_stats["p50"] is not None:
            if infer_ms_stats["p50"] > 0:
                ratio = infer_ms_stats["p95"] / infer_ms_stats["p50"]
                if ratio >= 3.0:
                    warnings.append(
                        f"INFER_MS p95/p50 ratio too high: {ratio:.2f} (p95={infer_ms_stats['p95']:.1f}ms, p50={infer_ms_stats['p50']:.1f}ms)"
                    )

        return BenchmarkReport(
            timestamp=datetime.now().isoformat(),
            total_requests=len(results),
            successful_requests=len(successful),
            failed_requests=len(failed),
            requests=[asdict(r) for r in results],
            mode_counts=mode_counts,
            fallback_triggered_count=fallback_triggered,
            fallback_blocked_count=fallback_blocked,
            infer_ms_stats=infer_ms_stats,
            ttft_ms_stats=ttft_ms_stats,
            tps_stats=tps_stats,
            tps_approx_stats=tps_approx_stats,
            outliers=outliers,
            warnings=warnings,
        )

    def save_report(self, report: BenchmarkReport, output_dir: Path) -> Path:
        """리포트 저장"""
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_dir / f"mode_benchmark_{timestamp_str}.json"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2, ensure_ascii=False)

        return json_path

    def print_summary(self, report: BenchmarkReport) -> None:
        """인간이 읽을 수 있는 요약 출력"""
        print("\n" + "=" * 80)
        print("Mode Distribution Benchmark Summary")
        print("=" * 80)
        print(f"Timestamp: {report.timestamp}")
        print(f"Total Requests: {report.total_requests}")
        print(f"Successful: {report.successful_requests}")
        print(f"Failed: {report.failed_requests}")

        print("\n--- Mode Distribution ---")
        for mode, count in report.mode_counts.items():
            percent = (count / report.successful_requests * 100) if report.successful_requests > 0 else 0
            print(f"  {mode}: {count} ({percent:.1f}%)")

        print("\n--- Fallback Statistics ---")
        print(f"  Triggered: {report.fallback_triggered_count}")
        print(f"  Blocked: {report.fallback_blocked_count}")

        print("\n--- Performance Metrics ---")
        if report.infer_ms_stats["p50"] is not None:
            print(f"  INFER_MS:")
            print(f"    p50: {report.infer_ms_stats['p50']:.1f}ms")
            print(f"    p95: {report.infer_ms_stats['p95']:.1f}ms")
            print(f"    mean: {report.infer_ms_stats['mean']:.1f}ms")

        if report.ttft_ms_stats["p50"] is not None:
            print(f"  TTFT_MS (measured only):")
            print(f"    p50: {report.ttft_ms_stats['p50']:.1f}ms")
            print(f"    p95: {report.ttft_ms_stats['p95']:.1f}ms")
            print(f"    mean: {report.ttft_ms_stats['mean']:.1f}ms")

        if report.tps_stats["p50"] is not None:
            print(f"  TPS:")
            print(f"    p50: {report.tps_stats['p50']:.2f} tokens/s")
            print(f"    p95: {report.tps_stats['p95']:.2f} tokens/s")
            print(f"    mean: {report.tps_stats['mean']:.2f} tokens/s")

        if report.tps_approx_stats["p50"] is not None:
            print(f"  TPS_APPROX:")
            print(f"    p50: {report.tps_approx_stats['p50']:.2f} tokens/s")
            print(f"    p95: {report.tps_approx_stats['p95']:.2f} tokens/s")
            print(f"    mean: {report.tps_approx_stats['mean']:.2f} tokens/s")

        if report.outliers:
            print("\n--- Top 5 INFER_MS Outliers ---")
            for i, outlier in enumerate(report.outliers, 1):
                print(f"  {i}. Index {outlier['index']}: {outlier['infer_ms']:.1f}ms - {outlier['prompt']}")

        if report.warnings:
            print("\n--- Warnings ---")
            for warning in report.warnings:
                print(f"  ⚠️  {warning}")
        else:
            print("\n--- No Warnings ---")

        print("=" * 80)

    async def run(self) -> Path:
        """벤치마크 실행"""
        print("=" * 80)
        print("Mode Distribution Benchmark Runner")
        print("=" * 80)
        print(f"API URL: {self.api_url}")
        print(f"Total Prompts: 30 (10 fast, 10 tool, 10 thinking)")
        print("=" * 80)

        # 모든 프롬프트 수집
        all_prompts = []
        for category in ["fast", "tool", "thinking"]:
            for prompt in PROMPTS[category]:
                all_prompts.append((prompt, category))

        # 순차적으로 요청 전송
        for idx, (prompt, category) in enumerate(all_prompts, 1):
            print(f"\n[{idx}/30] Sending: {prompt[:50]}... ({category})")
            result = await self.send_request(idx, prompt, category)
            self.results.append(result)

            if result.error:
                print(f"  ❌ Error: {result.error}")
            else:
                print(f"  ✅ Success (session_id={result.session_id}, message_id={result.message_id})")

            # DB에서 추가 메트릭 조회
            await self.enrich_with_db_metrics(result)

            # 요청 간 짧은 대기 (시스템 부하 방지)
            await asyncio.sleep(0.5)

        # 리포트 생성
        report = self.compute_statistics(self.results)
        self.print_summary(report)

        # 리포트 저장
        output_dir = Path(__file__).parent.parent / "outputs" / "bench"
        json_path = self.save_report(report, output_dir)
        print(f"\n✅ Report saved to: {json_path}")

        return json_path


async def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description="Mode Distribution Benchmark Runner")
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--auth-token",
        type=str,
        default=None,
        help="Authorization token (Bearer token)",
    )
    args = parser.parse_args()

    runner = BenchmarkRunner(api_url=args.api_url, auth_token=args.auth_token)
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
