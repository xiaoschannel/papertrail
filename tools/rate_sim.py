"""A stand-in OpenAI that enforces rate limits, to see how a run paces itself against one.

Not part of the test suite: it runs in real time, for a minute or two at a stretch. It answers the
endpoint the extractor calls, with the limits of whichever tier is asked for, a latency per call, and
the same rate-limit headers the real one sends. Calls over the limit are refused with 429 and a
Retry-After, so the path that exists for when the pacing is wrong is exercised rather than assumed.

    python tools/rate_sim.py                                  # 300 documents, a large account's limits
    python tools/rate_sim.py --rpm 5000 --tpm 2000000         # the limits your own account reports
    python tools/rate_sim.py --size small --documents 60
    python tools/rate_sim.py --latency 3.0                    # a slower model: more calls in flight

What it prints: a second-by-second timeline of requests, tokens, calls in flight and the pace the run
chose, then whether the run stayed inside the limits and how much of them it used. A run should sit
near the margin -- far under it is speed left on the table, over it is a 429 waiting to happen.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: Sizes of limit worth running against, per minute. These are shapes to test, not anybody's tier:
#: nothing reports which tier an account is on, only what each model's limits are, so use
#: `python tools/rate_limits.py` for the real ones and `--rpm/--tpm` to simulate them.
SIZES = {"small": (500, 200_000), "medium": (5_000, 450_000), "large": (5_000, 2_000_000)}


@dataclass
class Window:
    """A minute of allowance that refills continuously, as a provider's limiter does."""

    limit: int
    used: deque = field(default_factory=deque)     # (when, how much), for the last minute

    def _trim(self, now: float) -> None:
        while self.used and now - self.used[0][0] > 60:
            self.used.popleft()

    def left(self, now: float) -> int:
        self._trim(now)
        return self.limit - sum(amount for _, amount in self.used)

    def take(self, now: float, amount: int) -> bool:
        if self.left(now) < amount:
            return False
        self.used.append((now, amount))
        return True

    def reset_in(self, now: float) -> float:
        self._trim(now)
        return max(0.0, 60 - (now - self.used[0][0])) if self.used else 0.0


class Provider:
    """The limits, what has been spent against them, and a record of every call for the timeline."""

    def __init__(self, requests_per_minute: int, tokens_per_minute: int, latency: float, tokens: int,
                 overstate: float = 1.0):
        self.requests = Window(requests_per_minute)
        self.tokens = Window(tokens_per_minute)
        self.overstate = overstate                 # headers claiming more room than is enforced
        self.latency = latency
        self.tokens_per_call = tokens
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.events: list[dict] = []               # one per call: when, how long, refused or not
        self.in_flight = 0
        self.peak_in_flight = 0

    def call(self) -> tuple[int, dict, float]:
        """One request: (status, headers, seconds to hold it for). 429 when it doesn't fit."""
        now = time.monotonic()
        with self.lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            room = self.requests.take(now, 1) and self.tokens.take(now, self.tokens_per_call)
            headers = self._headers(now)
            if not room:
                self.in_flight -= 1
                self.events.append({"at": now - self.started, "seconds": 0, "refused": True})
                headers["retry-after"] = str(int(max(1, self.tokens.reset_in(now))))
                return 429, headers, 0.0
        return 200, headers, self.latency

    def finished(self, at: float, seconds: float) -> None:
        with self.lock:
            self.in_flight -= 1
            self.events.append({"at": at - self.started, "seconds": seconds, "refused": False})

    def _headers(self, now: float) -> dict[str, str]:
        claimed = self.overstate
        return {"x-ratelimit-limit-requests": str(int(self.requests.limit * claimed)),
                "x-ratelimit-remaining-requests": str(int(max(0, self.requests.left(now)) * claimed)),
                "x-ratelimit-reset-requests": f"{self.requests.reset_in(now):.1f}s",
                "x-ratelimit-limit-tokens": str(int(self.tokens.limit * claimed)),
                "x-ratelimit-remaining-tokens": str(int(max(0, self.tokens.left(now)) * claimed)),
                "x-ratelimit-reset-tokens": f"{self.tokens.reset_in(now):.1f}s"}


def handler_for(provider: Provider):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):                          # noqa: N802 - the base class names it
            self.rfile.read(int(self.headers.get("content-length", 0)))
            status, headers, hold = provider.call()
            started = time.monotonic()
            if hold:
                time.sleep(hold)
            body = _body(provider.tokens_per_call) if status == 200 else {"error": {"message": "rate limit"}}
            payload = json.dumps(body).encode()
            self.send_response(status)
            for name, value in {**headers, "content-type": "application/json",
                                "content-length": str(len(payload))}.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)
            if status == 200:
                provider.finished(time.monotonic(), time.monotonic() - started)

        def log_message(self, *args):               # the timeline is the output; access logs are noise
            pass

    return Handler


def _body(tokens: int) -> dict:
    """A chat completion carrying an extraction, as the SDK's structured-output parser expects."""
    content = json.dumps({"document_type": "receipt", "language": "ja", "date": "2026-01-01",
                          "time": "10:00", "name": "Simulated Shop", "phone": "", "currency": "JPY",
                          "address": "", "items": [], "cost": 100.0, "field_sources": []})
    return {"id": "chatcmpl-sim", "object": "chat.completion", "created": int(time.time()),
            "model": "gpt-5.6-luna", "system_fingerprint": "fp_sim", "service_tier": "default",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content, "refusal": None}}],
            "usage": {"prompt_tokens": int(tokens * 0.8), "completion_tokens": int(tokens * 0.2),
                      "total_tokens": tokens,
                      "prompt_tokens_details": {"cached_tokens": int(tokens * 0.6)},
                      "completion_tokens_details": {"reasoning_tokens": int(tokens * 0.1)}}}


def timeline(provider: Provider, paces: list[tuple[float, int]]) -> str:
    """A second per row: calls started, tokens spent, and how many the run had in flight."""
    if not provider.events:
        return "nothing ran"
    span = int(max(event["at"] for event in provider.events)) + 1
    rows = []
    for second in range(span):
        started = [e for e in provider.events if int(e["at"]) == second]
        refused = sum(1 for e in started if e["refused"])
        allowed = len(started) - refused
        pace = next((pace for when, pace in reversed(paces) if when <= second + 1), 0)
        bar = "#" * min(60, allowed)
        rows.append(f"{second:>4}s {allowed:>4} calls {allowed * provider.tokens_per_call:>8} tok "
                    f"pace {pace:>3} {bar}{'  REFUSED x' + str(refused) if refused else ''}")
    return "\n".join(rows)


def _busiest_minute(times: list[float], elapsed: float) -> float:
    """Calls a minute at the run's busiest: the most in any 60s window, or the whole run scaled up."""
    if not times:
        return 0.0
    if elapsed < 60:
        return len(times) / max(elapsed, 0.001) * 60
    times = sorted(times)
    most, start = 0, 0
    for end, when in enumerate(times):
        while when - times[start] > 60:
            start += 1
        most = max(most, end - start + 1)
    return float(most)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--size", default="large", choices=sorted(SIZES),
                        help="a shape of limit to run against; --rpm/--tpm override it")
    parser.add_argument("--rpm", type=int, help="requests a minute the provider enforces")
    parser.add_argument("--tpm", type=int, help="tokens a minute the provider enforces")
    parser.add_argument("--documents", type=int, default=300)
    parser.add_argument("--latency", type=float, default=1.5, help="seconds a call takes")
    parser.add_argument("--tokens", type=int, default=1_500, help="tokens a call spends")
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--overstate", type=float, default=1.0,
                        help="have the headers claim this multiple of the limit actually enforced, so the "
                             "run paces itself wrongly and the 429 path is exercised")
    args = parser.parse_args()

    requests_per_minute, tokens_per_minute = SIZES[args.size]
    requests_per_minute = args.rpm or requests_per_minute
    tokens_per_minute = args.tpm or tokens_per_minute
    provider = Provider(requests_per_minute, tokens_per_minute, args.latency, args.tokens, args.overstate)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(provider))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    import os

    os.environ["OPENAI_BASE_URL"] = f"http://127.0.0.1:{args.port}/v1"
    os.environ.setdefault("OPENAI_API_KEY", "sk-simulated")

    import extraction
    from rate_budget import MARGIN, RateBudget

    extraction.budget = RateBudget()
    budget = extraction.budget
    paces: list[tuple[float, int]] = []
    stop = threading.Event()

    def watch():
        while not stop.wait(0.2):
            paces.append((time.monotonic() - provider.started, budget.slots))

    threading.Thread(target=watch, daemon=True).start()

    print(f"limits: {requests_per_minute}/min requests, {tokens_per_minute}/min tokens; "
          f"{args.documents} documents at {args.latency}s and {args.tokens} tokens each")
    started = time.monotonic()
    _run(args.documents)
    elapsed = time.monotonic() - started
    stop.set()
    server.shutdown()

    print()
    print(timeline(provider, paces))
    done = [e for e in provider.events if not e["refused"]]
    refused = [e for e in provider.events if e["refused"]]
    busiest = _busiest_minute([e["at"] for e in done], elapsed)
    print()
    print(f"{len(done)} calls in {elapsed:.1f}s, {len(refused)} refused, peak {provider.peak_in_flight} in flight")
    if elapsed < 60:
        print(f"(the run was shorter than a minute; the rates below are its busiest {elapsed:.0f}s, scaled up)")
    print(f"requests: {busiest:>8.0f}/min of {requests_per_minute} "
          f"({busiest / requests_per_minute:.0%}, aiming for {MARGIN:.0%})")
    print(f"tokens:   {busiest * args.tokens:>8.0f}/min of {tokens_per_minute} "
          f"({busiest * args.tokens / tokens_per_minute:.0%}, aiming for {MARGIN:.0%})")
    print(f"pace ended at {budget.slots} call(s) at a time; the limits allowed {budget.target()}")
    return 1 if refused else 0


def _run(documents: int) -> None:
    """A Parse run over a throwaway copy of the test fixtures, against the stand-in provider.

    The pipeline itself, not a stand-in for it: the same pool, the same waiting on the budget, the same
    retry when a call is refused -- so what the timeline shows is what a real run would do.
    """
    import shutil
    import tempfile

    import ingest_pipeline as pipeline
    from rate_budget import MAX_SLOTS
    from extraction import EXTRACTORS

    box = Path(tempfile.mkdtemp()) / "ingest"
    shutil.copytree(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ingest", box)
    plan = pipeline.plan_parse(box, reprocess=True, limit=0)
    if not plan.documents:
        raise SystemExit("the fixture has no documents to parse")
    # The fixture holds a handful; a run worth watching needs hundreds, so they come round again.
    plan.documents = [plan.documents[i % len(plan.documents)] for i in range(documents)]

    pipeline.run_parse(box, plan, EXTRACTORS["OpenAI - gpt-5.6-luna"], "", _Progress(),
                       model="OpenAI - gpt-5.6-luna", workers=MAX_SLOTS, shuffle=False, save_every=1e9)
    shutil.rmtree(box.parent, ignore_errors=True)


class _Progress:
    """What a job would give the pipeline, reduced to what a simulation needs."""

    cancelled = False
    job_id = "simulation"

    def set_total(self, total: int) -> None:
        pass

    def tick(self, ok: bool = True, item: str = "", error: str = "") -> None:
        if not ok:
            print(f"   failed: {item}: {error}", flush=True)

    def say(self, message: str) -> None:
        print(f"   {message}", flush=True)

    def record(self, run) -> None:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
