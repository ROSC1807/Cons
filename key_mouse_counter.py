#!/usr/bin/env python3
"""Global keyboard/mouse counter for macOS.

Counts events only (does NOT record which keys were pressed).
Saves counts to a JSON file periodically and on shutdown.

Requires: pynput
macOS note: you must grant Accessibility (and sometimes Input Monitoring)
permission to the app launching Python (e.g., Terminal, iTerm, VS Code).
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


DEFAULT_OUTFILE = os.path.join(os.path.dirname(__file__), "input_counts.json")
SCHEMA_VERSION = 2
HISTORY_RETENTION_MINUTES = 24 * 60
COUNT_INT_FIELDS = (
    "keyboard_presses",
    "mouse_clicks_total",
    "mouse_clicks_left",
    "mouse_clicks_right",
    "mouse_clicks_middle",
)
SESSION_INT_FIELDS = (
    "session_keyboard_presses",
    "session_mouse_clicks_total",
    "session_mouse_clicks_left",
    "session_mouse_clicks_right",
    "session_mouse_clicks_middle",
)
TIMESTAMP_FIELDS = ("started_at_utc", "last_event_at_utc", "updated_at_utc")
NowProvider = Callable[[], datetime]


@dataclass
class MinuteBucket:
    bucket_start_utc: str
    keyboard_presses: int = 0
    mouse_clicks_total: int = 0
    mouse_clicks_left: int = 0
    mouse_clicks_right: int = 0
    mouse_clicks_middle: int = 0


@dataclass
class Counts:
    schema_version: int = SCHEMA_VERSION
    keyboard_presses: int = 0
    mouse_clicks_total: int = 0
    mouse_clicks_left: int = 0
    mouse_clicks_right: int = 0
    mouse_clicks_middle: int = 0
    started_at_utc: str = ""
    last_event_at_utc: str = ""
    updated_at_utc: str = ""
    session_started_at_utc: str = ""
    session_keyboard_presses: int = 0
    session_mouse_clicks_total: int = 0
    session_mouse_clicks_left: int = 0
    session_mouse_clicks_right: int = 0
    session_mouse_clicks_middle: int = 0
    history_minutes: list[MinuteBucket] = field(default_factory=list)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")


def minute_start(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def minute_key(dt: datetime) -> str:
    return now_iso(minute_start(dt))


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def atomic_write_json(path: str, data: dict[str, Any]) -> None:
    """Write JSON atomically to avoid torn writes."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".counts-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


class Counter:
    def __init__(self, outfile: str, now_provider: NowProvider | None = None) -> None:
        self.outfile = outfile
        self._now = now_provider or utc_now
        self.lock = threading.Lock()
        current_time = self._now()
        self.counts = Counts(
            schema_version=SCHEMA_VERSION,
            started_at_utc=now_iso(current_time),
            updated_at_utc=now_iso(current_time),
            session_started_at_utc=now_iso(current_time),
        )
        self._dirty = False
        self._load_existing()

    def _load_existing(self) -> None:
        if not os.path.exists(self.outfile):
            return
        try:
            with open(self.outfile, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            return
        if not isinstance(obj, dict):
            return

        with self.lock:
            self._load_totals_locked(obj)
            self._load_history_locked(obj.get("history_minutes"))
            current_time = self._now()
            self.counts.schema_version = SCHEMA_VERSION
            self.counts.session_started_at_utc = now_iso(current_time)
            for field_name in SESSION_INT_FIELDS:
                setattr(self.counts, field_name, 0)
            if not self.counts.started_at_utc:
                self.counts.started_at_utc = now_iso(current_time)
            self.counts.updated_at_utc = now_iso(current_time)
            self._prune_history_locked(current_time)

    def _load_totals_locked(self, obj: dict[str, Any]) -> None:
        for field_name in COUNT_INT_FIELDS:
            value = obj.get(field_name)
            if isinstance(value, int):
                setattr(self.counts, field_name, value)
        for field_name in TIMESTAMP_FIELDS:
            value = obj.get(field_name)
            if isinstance(value, str):
                setattr(self.counts, field_name, value)

    def _load_history_locked(self, raw_history: Any) -> None:
        if not isinstance(raw_history, list):
            return

        merged: dict[str, MinuteBucket] = {}
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            bucket_dt = parse_iso_datetime(item.get("bucket_start_utc"))
            if bucket_dt is None:
                continue
            key = minute_key(bucket_dt)
            bucket = merged.setdefault(key, MinuteBucket(bucket_start_utc=key))
            for field_name in COUNT_INT_FIELDS:
                value = item.get(field_name)
                if isinstance(value, int):
                    setattr(bucket, field_name, getattr(bucket, field_name) + value)

        self.counts.history_minutes = [merged[key] for key in sorted(merged)]

    def _bucket_for_now_locked(self, current_time: datetime) -> MinuteBucket:
        key = minute_key(current_time)
        history = self.counts.history_minutes
        for index in range(len(history) - 1, -1, -1):
            bucket = history[index]
            if bucket.bucket_start_utc == key:
                return bucket
            if bucket.bucket_start_utc < key:
                new_bucket = MinuteBucket(bucket_start_utc=key)
                history.insert(index + 1, new_bucket)
                return new_bucket
        new_bucket = MinuteBucket(bucket_start_utc=key)
        history.insert(0, new_bucket)
        return new_bucket

    def _prune_history_locked(self, current_time: datetime | None = None) -> None:
        active_time = current_time or self._now()
        cutoff = minute_start(active_time) - timedelta(minutes=HISTORY_RETENTION_MINUTES - 1)
        pruned: list[MinuteBucket] = []
        for bucket in self.counts.history_minutes:
            bucket_time = parse_iso_datetime(bucket.bucket_start_utc)
            if bucket_time is None or bucket_time < cutoff:
                continue
            pruned.append(bucket)
        self.counts.history_minutes = pruned

    def _touch_locked(self, current_time: datetime) -> None:
        current_iso = now_iso(current_time)
        self.counts.last_event_at_utc = current_iso
        self.counts.updated_at_utc = current_iso
        self._prune_history_locked(current_time)
        self._dirty = True

    def on_key_press(self) -> None:
        with self.lock:
            current_time = self._now()
            self.counts.keyboard_presses += 1
            self.counts.session_keyboard_presses += 1
            bucket = self._bucket_for_now_locked(current_time)
            bucket.keyboard_presses += 1
            self._touch_locked(current_time)

    def on_click(self, button: Any, pressed: bool) -> None:
        if not pressed:
            return

        button_name = self._normalize_button_name(button)
        with self.lock:
            current_time = self._now()
            self.counts.mouse_clicks_total += 1
            self.counts.session_mouse_clicks_total += 1
            bucket = self._bucket_for_now_locked(current_time)
            bucket.mouse_clicks_total += 1

            if button_name == "left":
                self.counts.mouse_clicks_left += 1
                self.counts.session_mouse_clicks_left += 1
                bucket.mouse_clicks_left += 1
            elif button_name == "right":
                self.counts.mouse_clicks_right += 1
                self.counts.session_mouse_clicks_right += 1
                bucket.mouse_clicks_right += 1
            elif button_name == "middle":
                self.counts.mouse_clicks_middle += 1
                self.counts.session_mouse_clicks_middle += 1
                bucket.mouse_clicks_middle += 1

            self._touch_locked(current_time)

    def _normalize_button_name(self, button: Any) -> str:
        if isinstance(button, str):
            name = button
        else:
            name = getattr(button, "name", str(button))
        return name.rsplit(".", 1)[-1].lower()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            current_time = self._now()
            self.counts.schema_version = SCHEMA_VERSION
            self.counts.updated_at_utc = now_iso(current_time)
            self._prune_history_locked(current_time)
            return asdict(self.counts)

    def save_if_dirty(self) -> bool:
        with self.lock:
            if not self._dirty:
                return False
            self._dirty = False
        atomic_write_json(self.outfile, self.snapshot())
        return True

    def save_force(self) -> None:
        atomic_write_json(self.outfile, self.snapshot())


def print_listener_error(error: Exception) -> None:
    print(
        "Failed to start listeners. On macOS you must grant Accessibility permission "
        "(and sometimes Input Monitoring) to the app running Python.\n"
        f"Error: {error}",
        file=sys.stderr,
    )


def run_listener_loop(counter: Counter, stop_event: threading.Event, ready_event: threading.Event, errors: list[Exception]) -> None:
    try:
        from pynput import keyboard, mouse

        def on_key_press(_key: Any) -> None:
            counter.on_key_press()

        def on_click(_x: Any, _y: Any, button: Any, pressed: bool) -> None:
            counter.on_click(button=button, pressed=pressed)

        with keyboard.Listener(on_press=on_key_press) as k_listener, mouse.Listener(on_click=on_click) as m_listener:
            ready_event.set()
            while not stop_event.is_set():
                time.sleep(0.2)
            k_listener.stop()
            m_listener.stop()
    except Exception as error:
        errors.append(error)
        ready_event.set()
        stop_event.set()


def run_flusher(counter: Counter, stop_event: threading.Event, flush_interval: float, print_interval: float) -> None:
    last_print = 0.0
    while not stop_event.is_set():
        counter.save_if_dirty()
        if print_interval and (time.monotonic() - last_print) >= print_interval:
            last_print = time.monotonic()
            print(json.dumps(counter.snapshot(), ensure_ascii=False))
            sys.stdout.flush()
        stop_event.wait(flush_interval)
    try:
        counter.save_force()
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Count global keyboard and mouse events on macOS.")
    ap.add_argument("--file", default=DEFAULT_OUTFILE, help=f"Output JSON file (default: {DEFAULT_OUTFILE})")
    ap.add_argument("--flush-interval", type=float, default=5.0, help="Seconds between saves (default: 5)")
    ap.add_argument("--print-interval", type=float, default=0.0, help="If >0, print counts every N seconds")
    ap.add_argument("--gui", action="store_true", help="Show a realtime desktop dashboard window")
    args = ap.parse_args()

    if args.flush_interval <= 0:
        ap.error("--flush-interval must be > 0")
    if args.print_interval < 0:
        ap.error("--print-interval must be >= 0")

    counter = Counter(outfile=args.file)
    stop_event = threading.Event()
    listener_ready = threading.Event()
    listener_errors: list[Exception] = []

    def handle_stop(_signum: int | None = None, _frame: Any = None) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    flusher_thread = threading.Thread(
        target=run_flusher,
        args=(counter, stop_event, args.flush_interval, args.print_interval),
        name="flusher",
        daemon=True,
    )
    listener_thread = threading.Thread(
        target=run_listener_loop,
        args=(counter, stop_event, listener_ready, listener_errors),
        name="listeners",
        daemon=True,
    )

    flusher_thread.start()
    listener_thread.start()
    atexit.register(counter.save_force)
    listener_ready.wait(timeout=2.0)

    if listener_errors:
        print_listener_error(listener_errors[0])
        return 2

    try:
        if args.gui:
            try:
                from dashboard import run_dashboard
            except Exception as error:
                print(f"Failed to launch dashboard: {error}", file=sys.stderr)
                return 2
            run_dashboard(counter, stop_event)
        else:
            while not stop_event.is_set():
                time.sleep(0.2)
    finally:
        stop_event.set()
        listener_thread.join(timeout=1.0)
        flusher_thread.join(timeout=max(1.0, args.flush_interval + 1.0))
        try:
            counter.save_force()
        except Exception:
            pass

    if listener_errors:
        print_listener_error(listener_errors[0])
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
