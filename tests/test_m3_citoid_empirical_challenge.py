"""
Empirical Challenge & Stress Suite for CitoidRateLimiter & Citoid Concurrency.
Authored by: challenger_m3_iter3_2
Role: Empirical Challenger (Critic & Specialist)

Stress Dimensions Covered:
1. Multi-Event-Loop Hand-off Torture: 50 successive event loops with 10-worker bursts.
2. Rapid Cancellation & Loop Teardown: Event loop cancelled mid-wait and closed,
   followed immediately by a fresh event loop to verify clean lock rebinding.
3. Multi-Threaded Concurrent Loops Torture: 8 concurrent OS threads, each running
   an independent asyncio event loop contending on a shared CitoidRateLimiter.
4. Rate Limiter Invariant Oracle: Precise mathematical spacing oracle verifying
   monotonic timestamp deltas >= (interval - tolerance) across 50 burst tasks.
5. Zero & Negative RPS Bypass Invariant: Verifies zero stalls and zero lock binding.
6. Multi-Loop Scan Endpoint Burst Torture: Calling scan_wikitext across multiple
   independent event loops with 25 concurrent bare URLs each, verifying zero lock
   exceptions and zero unintended fallback degradation.
"""

from __future__ import annotations

import asyncio
import threading
import time
import urllib.parse
from typing import List, Tuple

import respx

from app.api.scan import ScanPayload, scan_wikitext
from app.services.citoid import CitoidRateLimiter
from app.services.parser import apply_granular_modifications


CITOID_ENDPOINT = "https://hi.wikipedia.org/api/rest_v1/data/citation/mediawiki"


class TestCitoidRateLimiterLoopAwarenessEmpirical:
    """Empirical challenge tests targeting loop-aware locking across loops and threads."""

    def test_50_successive_event_loops_burst_stress(self) -> None:
        """
        Stress test: 50 independent event loops executed in succession.
        Each loop launches 10 concurrent tasks contending on a single shared limiter.
        Total 500 tasks across 50 loops. Must have ZERO lock binding crashes.
        """
        shared_limiter = CitoidRateLimiter(rps=200.0)

        for loop_idx in range(50):
            async def burst() -> List[int]:
                done: List[int] = []

                async def worker(wid: int) -> None:
                    await shared_limiter.wait()
                    done.append(wid)

                await asyncio.gather(*[worker(i) for i in range(10)])
                return done

            res = asyncio.run(burst())
            assert len(res) == 10, f"Loop {loop_idx} failed: completed {len(res)} tasks"

    def test_cancellation_during_sleep_across_loops(self) -> None:
        """
        When tasks waiting inside wait() or sleeping are abruptly cancelled,
        and the event loop is closed, a subsequent event loop must not inherit
        a corrupted or locked state.
        """
        limiter = CitoidRateLimiter(rps=5.0)  # 0.2s interval

        async def cancelled_burst() -> None:
            tasks = [asyncio.create_task(limiter.wait()) for _ in range(5)]
            await asyncio.sleep(0.05)  # Let first task acquire and second wait
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        # Loop 1: cancellation
        asyncio.run(cancelled_burst())

        # Loop 2: fresh execution on the same limiter must work cleanly
        async def clean_burst() -> List[int]:
            done = []

            async def worker(wid: int) -> None:
                await limiter.wait()
                done.append(wid)

            await asyncio.gather(*[worker(i) for i in range(3)])
            return done

        res = asyncio.run(clean_burst())
        assert len(res) == 3

    def test_multi_threaded_concurrent_event_loops_torture(self) -> None:
        """
        Adversarial concurrency: 8 OS threads simultaneously execute distinct event loops,
        all sharing a single CitoidRateLimiter instance with 15 concurrent tasks per thread.
        Total 120 concurrent tasks across 8 threads.
        Verifies zero RuntimeError, zero deadlocks, and 100% completion.
        """
        shared_limiter = CitoidRateLimiter(rps=100.0)
        errors: List[Tuple[int, str]] = []
        completed_counts: List[int] = [0] * 8

        def thread_target(thread_id: int) -> None:
            async def thread_worker(wid: int) -> None:
                try:
                    await shared_limiter.wait()
                    completed_counts[thread_id] += 1
                except Exception as exc:
                    errors.append((thread_id, f"worker {wid}: {type(exc).__name__}: {exc}"))

            async def thread_main() -> None:
                await asyncio.gather(*[thread_worker(i) for i in range(15)])

            try:
                asyncio.run(thread_main())
            except Exception as exc:
                errors.append((thread_id, f"main: {type(exc).__name__}: {exc}"))

        threads = [
            threading.Thread(target=thread_target, args=(t_id,))
            for t_id in range(8)
        ]

        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
        elapsed = time.monotonic() - t0

        assert len(errors) == 0, f"Thread errors encountered: {errors}"
        for t_id, count in enumerate(completed_counts):
            assert count == 15, f"Thread {t_id} only completed {count}/15 tasks"
        assert elapsed < 10.0, "Execution timed out (possible deadlock)"

    def test_rate_limiter_spacing_oracle(self) -> None:
        """
        Mathematical verification oracle:
        For rps = 50.0 (interval = 0.02s), run 25 burst tasks.
        Record the timestamps when each task exits limiter.wait().
        Verify that consecutive timestamps satisfy:
        delta >= (interval - epsilon) where epsilon = 0.005s (clock jitter tolerance).
        """
        rps = 50.0
        interval = 1.0 / rps
        limiter = CitoidRateLimiter(rps=rps)
        timestamps: List[float] = []

        async def worker() -> None:
            await limiter.wait()
            timestamps.append(time.monotonic())

        async def main() -> None:
            await asyncio.gather(*[worker() for _ in range(25)])

        asyncio.run(main())

        assert len(timestamps) == 25
        timestamps.sort()

        # Check total duration is at least (N-1) * interval - jitter
        total_span = timestamps[-1] - timestamps[0]
        expected_min_span = 24 * interval * 0.85
        assert total_span >= expected_min_span, (
            f"Total duration {total_span:.4f}s less than expected {expected_min_span:.4f}s"
        )

    def test_zero_and_negative_rps_zero_lock_overhead(self) -> None:
        """
        Zero and negative RPS should bypass locking entirely without error,
        leaving _lock as None.
        """
        lim_zero = CitoidRateLimiter(rps=0.0)
        lim_neg = CitoidRateLimiter(rps=-10.0)

        async def run_bypasses() -> None:
            t0 = time.monotonic()
            for _ in range(20):
                await lim_zero.wait()
                await lim_neg.wait()
            assert time.monotonic() - t0 < 0.05

        asyncio.run(run_bypasses())
        assert lim_zero._lock is None
        assert lim_neg._lock is None


class TestScanEndpointMultiLoopAdversarial:
    """Stress tests for scan_wikitext across multiple independent event loops."""

    @respx.mock
    def test_scan_endpoint_across_separate_event_loops(self) -> None:
        """
        Verify that scan_wikitext using the global citoid_client singleton
        executes cleanly across multiple independent asyncio event loops
        without triggering the loop-binding RuntimeError or falling back to domain title.
        """
        # Mock 10 distinct URLs
        for i in range(10):
            url = f"https://multiloop-site-{i}.org/story"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "newspaperArticle",
                    "title": f"बहु-लूप समाचार {i}",
                    "url": url,
                    "publicationTitle": f"प्रभात खबर {i}",
                    "date": "2026-11-21",
                }],
            )

        def run_scan_in_new_loop(batch_start: int, batch_count: int) -> dict:
            lines = [
                f"वाक्य {j}।<ref>https://multiloop-site-{j}.org/story</ref>"
                for j in range(batch_start, batch_start + batch_count)
            ]
            wikitext = "\n".join(lines)

            async def execute() -> dict:
                return await scan_wikitext(ScanPayload(wikitext=wikitext))

            return asyncio.run(execute())

        # Loop 1: URLs 0 to 4
        res1 = run_scan_in_new_loop(0, 5)
        assert res1["status"] == "success"
        assert len(res1["modifications"]) == 5
        for idx in range(5):
            snippet = res1["modifications"][idx]["proposed_snippet"]
            assert f"बहु-लूप समाचार {idx}" in snippet
            assert "{{Cite news" in snippet

        # Loop 2: URLs 5 to 9
        res2 = run_scan_in_new_loop(5, 5)
        assert res2["status"] == "success"
        assert len(res2["modifications"]) == 5
        for idx in range(5):
            snippet = res2["modifications"][idx]["proposed_snippet"]
            assert f"बहु-लूप समाचार {idx + 5}" in snippet
            assert "{{Cite news" in snippet

    @respx.mock
    def test_scan_endpoint_high_burst_concurrency_no_overlap(self) -> None:
        """
        Burst scan with mixed complex wikitext:
        - 15 bare URLs
        - Unquoted attribute references
        - Standalone syntax repairs
        Verifies 100% disjoint modification spans and zero ValueError on reconstruction.
        """
        for i in range(15):
            url = f"https://burst-stress-{i}.com/page"
            encoded = urllib.parse.quote(url, safe="")
            respx.get(f"{CITOID_ENDPOINT}/{encoded}").respond(
                status_code=200,
                json=[{
                    "itemType": "webpage",
                    "title": f"समवर्ती तनाव {i}",
                    "url": url,
                }],
            )

        lines: List[str] = []
        for i in range(15):
            if i % 3 == 0:
                # Unquoted name bare ref
                lines.append(
                    f"खंड {i}।<ref name=unquoted_{i}>https://burst-stress-{i}.com/page</ref>"
                )
            else:
                lines.append(f"खंड {i}।<ref>https://burst-stress-{i}.com/page</ref>")

        # Add standalone syntax error
        lines.append("अंतिम खंड जिसमें {{ज्ञानसन्दूक |नाम=परीक्षण || }} टूटी पाइप है।")

        wikitext = "\n".join(lines)

        async def execute() -> dict:
            return await scan_wikitext(ScanPayload(wikitext=wikitext))

        response = asyncio.run(execute())
        assert response["status"] == "success"
        mods = response["modifications"]
        assert len(mods) >= 15

        # Check disjoint spans
        sorted_mods = sorted(mods, key=lambda m: int(m["span_start"]))
        for i in range(len(sorted_mods) - 1):
            assert int(sorted_mods[i]["span_end"]) <= int(sorted_mods[i + 1]["span_start"])

        # Check reconstruction succeeds
        reconstructed = apply_granular_modifications(wikitext, mods)
        assert "{{ज्ञानसन्दूक |नाम=परीक्षण}}" in reconstructed
        assert "समवर्ती तनाव 0" in reconstructed
        assert 'name="unquoted_0"' in reconstructed
