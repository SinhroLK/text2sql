"""Conservative, durable local quota accounting (not account-wide usage)."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


class TokenQuota:
    def __init__(self, path: Path, *, tokens_per_minute=8000,
                 tokens_per_day=200000, safety_margin=0.9,
                 clock=time.time, sleep=time.sleep):
        if (not 0 < safety_margin < 1 or not math.isfinite(safety_margin)
                or tokens_per_minute <= 0 or tokens_per_day <= 0):
            raise ValueError("Quota limits must be positive and margin between zero and one")
        self.path, self.clock, self.sleep = path, clock, sleep
        self.windows = [(62, int(tokens_per_minute * safety_margin)),
                        (86402, int(tokens_per_day * safety_margin))]

    def wait(self, seconds, reason):
        deadline = self.clock() + seconds
        print(json.dumps({"event": "quota_pause", "reason": reason,
                          "resume_at_unix": deadline}), flush=True)
        while self.clock() < deadline:
            self.sleep(min(30, deadline - self.clock()))

    def reserve(self, tokens):
        # Called under the run journal's exclusive lock, before sending anything.
        if tokens <= 0 or any(tokens > limit for _, limit in self.windows):
            raise ValueError("One request exceeds the safe token quota; check quota settings")
        events = []
        if self.path.exists():
            events = [json.loads(line) for line in self.path.read_text().splitlines()]
            if any(set(e) != {"time", "tokens"} or not math.isfinite(e["time"])
                   or type(e["tokens"]) is not int or e["tokens"] <= 0 for e in events):
                raise ValueError("Invalid token quota ledger")
        while True:
            now = self.clock()
            delays = []
            for window, limit in self.windows:
                active = sorted((e for e in events if e["time"] + window > now),
                                key=lambda e: e["time"])
                used = sum(e["tokens"] for e in active)
                for event in active:
                    if used + tokens <= limit:
                        break
                    delays.append(event["time"] + window - now)
                    used -= event["tokens"]
            if not delays:
                break
            self.wait(max(delays), "local_token_budget")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as stream:
            stream.write(json.dumps({"time": self.clock(), "tokens": tokens}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
