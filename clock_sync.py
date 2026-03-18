#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Clock synchronization over Modbus TCP (FC16 - Write Multiple Registers)

Features:
- Config-driven (YAML or JSON): modes, NTP/system time source, offsets, logging, devices list…
- Supports Unit IDs per device, including unit_id=0 for some PLCs
- Addresses in decimal, contiguous [55..62] (seconds, minutes, hours, dow, dom, month, year, tz)
- Optional address base param (0 or 1) to adapt to different documentations
- Writes at the next "second == 0" to minimize drift across devices
- Test mode: read-before / write / read-after on the first device
- Debug mode: compute and print register table only, no network I/O
- No change to the local system clock. NTP is used only as a reference (SNTP).
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Third-party
try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusIOException
except ImportError:
    print("Missing dependency: pymodbus. Install with: pip install pymodbus", file=sys.stderr)
    sys.exit(2)

# YAML optional (preferred). Fallback to JSON if YAML not installed and config is JSON.
try:
    import yaml  # type: ignore
    HAVE_YAML = True
except Exception:
    HAVE_YAML = False


# ----------------------------
# Data model & constants
# ----------------------------

@dataclass
class Device:
    ip: str
    unit_id: int = 1
    port: int = 502
    enabled: bool = True
    timeout_s: Optional[float] = None  # optional per-device override

@dataclass
class Config:
    mode: str = "normal"  # "debug" | "test" | "normal"
    source_clock: str = "system"  # "system" | "ntp"
    ntp_servers: List[str] = None  # e.g. ["fr.pool.ntp.org", "pool.ntp.org"]
    offset_seconds: int = 0
    address_base: int = 0  # 0 if docs use base-0 addresses; use 1 if docs use base-1
    start_address: int = 55  # decimal start address for the Seconds register
    port: int = 502
    timeout_s: float = 3.0
    retries: int = 2
    log_file: str = "./clock-sync.log"
    log_level: str = "INFO"  # "DEBUG", "INFO", ...
    align_to_next_second_zero: bool = True
    verify_after_write: bool = False
    devices: List[Device] = None

    def __post_init__(self):
        if self.ntp_servers is None:
            self.ntp_servers = ["fr.pool.ntp.org", "pool.ntp.org"]
        if self.devices is None:
            self.devices = []


REG_COUNT = 8  # seconds, minutes, hours, dow, dom, month, year, tz
# Map indices for clarity
IDX_SECONDS = 0
IDX_MINUTES = 1
IDX_HOURS = 2
IDX_DOW = 3
IDX_DOM = 4
IDX_MONTH = 5
IDX_YEAR = 6
IDX_TZ = 7


# ----------------------------
# Utilities
# ----------------------------

def load_config(path: str) -> Config:
    if not os.path.isfile(path):
        print(f"Config file not found: {path}", file=sys.stderr)
        sys.exit(2)

    ext = os.path.splitext(path)[1].lower()
    with open(path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    data: Dict[str, Any]
    if ext in (".yaml", ".yml"):
        if not HAVE_YAML:
            print("PyYAML is required to read YAML config. Install with: pip install pyyaml", file=sys.stderr)
            sys.exit(2)
        data = yaml.safe_load(raw_text) or {}
    elif ext == ".json":
        data = json.loads(raw_text)
    else:
        # Try YAML first, then JSON
        if HAVE_YAML:
            try:
                data = yaml.safe_load(raw_text) or {}
            except Exception:
                data = json.loads(raw_text)
        else:
            data = json.loads(raw_text)

    # Normalize devices
    devices = []
    for dev in data.get("devices", []):
        devices.append(Device(
            ip=str(dev["ip"]),
            unit_id=int(dev.get("unit_id", 1)),
            port=int(dev.get("port", data.get("port", 502))),
            enabled=bool(dev.get("enabled", True)),
            timeout_s=float(dev["timeout_s"]) if dev.get("timeout_s") is not None else None,
        ))

    cfg = Config(
        mode=str(data.get("mode", "normal")).lower(),
        source_clock=str(data.get("source_clock", "system")).lower(),
        ntp_servers=list(data.get("ntp_servers", ["fr.pool.ntp.org", "pool.ntp.org"])),
        offset_seconds=int(data.get("offset_seconds", 0)),
        address_base=int(data.get("address_base", 0)),
        start_address=int(data.get("start_address", 55)),
        port=int(data.get("port", 502)),
        timeout_s=float(data.get("timeout_s", 3.0)),
        retries=int(data.get("retries", 2)),
        log_file=str(data.get("log_file", "./clock-sync.log")),
        log_level=str(data.get("log_level", "INFO")).upper(),
        align_to_next_second_zero=bool(data.get("align_to_next_second_zero", True)),
        verify_after_write=bool(data.get("verify_after_write", False)),
        devices=devices,
    )
    return cfg


def setup_logging(log_path: str, level: str, also_console: bool) -> None:
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(getattr(logging, level, logging.INFO))
    logger.addHandler(fh)

    # Console handler (for debug/test)
    if also_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        ch.setLevel(getattr(logging, level, logging.INFO))
        logger.addHandler(ch)


# ----------------------------
# Time sources
# ----------------------------

class TimeProvider:
    """Abstract time provider."""
    def now(self) -> datetime:
        raise NotImplementedError

class SystemTimeProvider(TimeProvider):
    def now(self) -> datetime:
        # Local time with timezone info (DST-aware)
        return datetime.now().astimezone()

class SNTPTimeProvider(TimeProvider):
    """SNTP-based time provider that does NOT modify the host system clock."""
    def __init__(self, servers: List[str], timeout: float = 3.0):
        self.servers = servers
        self.timeout = timeout
        self._epoch_secs = None  # float seconds since UNIX epoch at t0, in UTC
        self._t0 = None  # monotonic reference

        dt = self._query_ntp_once()
        self._epoch_secs = dt.timestamp()  # seconds (float) since epoch in local tz
        self._t0 = time.perf_counter()

    def _query_ntp_once(self) -> datetime:
        last_error = None
        for host in self.servers:
            try:
                dt = self._ntp_query(host)
                if dt:
                    return dt
            except Exception as e:
                last_error = e
        raise RuntimeError(f"NTP failed for servers: {self.servers}. Last error: {last_error}")

    def _ntp_query(self, host: str) -> Optional[datetime]:
        # SNTP (RFC 2030/4330): 48-byte request/response, big-endian
        # Transmit 0b00_100_011 = 0x1B in first byte (LI=0, VN=4, Mode=3)
        NTP_PACKET = b'\x1b' + 47 * b'\0'
        NTP_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            s.sendto(NTP_PACKET, (host, 123))
            data, _ = s.recvfrom(48)
            if len(data) < 48:
                return None

            # Transmit Timestamp starts at byte 40 (index 40..47)
            transmit_timestamp = data[40:48]
            sec, frac = struct.unpack("!II", transmit_timestamp)
            ntp_time = sec + float(frac) / (2**32)
            unix_time = ntp_time - NTP_DELTA

            # Return localized datetime (DST-aware)
            return datetime.fromtimestamp(unix_time, tz=datetime.now().astimezone().tzinfo)

    def now(self) -> datetime:
        # Use monotonic to advance from the initial NTP sample
        elapsed = time.perf_counter() - self._t0
        ts = self._epoch_secs + elapsed
        return datetime.fromtimestamp(ts, tz=datetime.now().astimezone().tzinfo)


def get_time_provider(cfg: Config) -> TimeProvider:
    if cfg.source_clock == "ntp":
        return SNTPTimeProvider(cfg.ntp_servers, timeout=cfg.timeout_s)
    return SystemTimeProvider()


def compute_timezone_hours(dt: datetime) -> int:
    """Compute local timezone hours offset from UTC, including DST, for the given datetime."""
    utcoff = dt.utcoffset() or timedelta(0)
    hours = int(round(utcoff.total_seconds() / 3600.0))
    # Clamp to [-12, +12]
    return max(-12, min(12, hours))


def compute_target_time(provider: TimeProvider, offset_seconds: int, align_to_next_second_zero: bool) -> datetime:
    """
    Compute the timestamp to write into PLCs:
    - If align_to_next_second_zero: target is the next minute boundary (second==0)
    - Else: target is the current time (rounded to nearest millisecond), with seconds preserved
    Then apply offset_seconds.
    """
    now = provider.now()
    if align_to_next_second_zero:
        # Next minute boundary
        target = (now.replace(second=0, microsecond=0) + timedelta(minutes=1))
    else:
        target = now
    # Apply offset to the data sent (not to system clock)
    target = target + timedelta(seconds=offset_seconds)
    return target


def wait_until_next_second_zero(provider: TimeProvider) -> None:
    """Sleep until the next moment when provider.now().second == 0."""
    now = provider.now()
    sec = now.second
    # Time until next minute boundary
    sleep_s = (60 - sec) + (0 - now.microsecond / 1_000_000.0)
    if sleep_s > 0:
        # Sleep most of it
        time.sleep(max(0.0, sleep_s - 0.200))
    # Busy-wait the last ~200ms for precision
    deadline = time.time() + 1.0  # safety
    while True:
        if provider.now().second == 0:
            break
        if time.time() > deadline:
            break
        time.sleep(0.005)


def build_register_values(target: datetime) -> List[int]:
    """
    Build the 8-register array based on the target datetime.
    Mapping (decimal addresses 55..62):
      55: seconds (0..59)
      56: minutes (0..59)
      57: hours   (0..23)
      58: day of week (0=Mon .. 6=Sun)
      59: day of month (1..31)
      60: month (1..12)
      61: year (00..99)
      62: timezone hours (-12..+12)
    """
    dow = target.isoweekday() % 7  # 1..7 -> 1..6,0
    tz_hours = compute_timezone_hours(target)

    regs = [0] * REG_COUNT
    regs[IDX_SECONDS] = int(target.second)            # 0..59
    regs[IDX_MINUTES] = int(target.minute)            # 0..59
    regs[IDX_HOURS]   = int(target.hour)              # 0..23
    regs[IDX_DOW]     = int(dow)                      # 0..6
    regs[IDX_DOM]     = int(target.day)               # 1..31
    regs[IDX_MONTH]   = int(target.month)             # 1..12
    regs[IDX_YEAR]    = int(target.year % 100)        # 0..99
    regs[IDX_TZ]      = int(tz_hours)                 # -12..+12
    return regs


def _effective_start_address(cfg: Config) -> int:
    """
    Convert documentation address to driver address, considering address_base param.
    If docs use base 1, we subtract 1 to pass base-0 to the library.
    """
    if cfg.address_base not in (0, 1):
        raise ValueError("address_base must be 0 or 1")
    return cfg.start_address - (1 if cfg.address_base == 1 else 0)


def _connect_client(ip: str, port: int, timeout_s: float) -> ModbusTcpClient:
    client = ModbusTcpClient(host=ip, port=port, timeout=timeout_s)
    ok = client.connect()
    if not ok:
        raise ConnectionError(f"Could not connect to {ip}:{port}")
    return client


def _read_registers(client: ModbusTcpClient, start: int, count: int, unit_id: int) -> Optional[List[int]]:
    try:
        resp = client.read_holding_registers(address=start, count=count, slave=unit_id)
        if hasattr(resp, "isError") and resp.isError():
            return None
        values = getattr(resp, "registers", None)
        if values is None or len(values) != count:
            return None
        return list(values)
    except (ModbusIOException, OSError):
        return None


def _write_registers(client: ModbusTcpClient, start: int, values: List[int], unit_id: int) -> bool:
    try:
        resp = client.write_registers(address=start, values=values, slave=unit_id)
        if hasattr(resp, "isError") and resp.isError():
            return False
        return True
    except (ModbusIOException, OSError):
        return False


def run_debug(cfg: Config, provider: TimeProvider) -> int:
    # Compute target timestamp (next :00 if configured)
    target = compute_target_time(provider, cfg.offset_seconds, cfg.align_to_next_second_zero)
    regs = build_register_values(target)

    logging.info("Mode=DEBUG: No write will be performed.")
    logging.info("Target timestamp: %s", target.isoformat())
    logging.info("Registers to write (start=%d, base=%d): %s",
                 cfg.start_address, cfg.address_base, regs)
    print("DEBUG - Register table (index: value):")
    labels = ["sec", "min", "hour", "dow", "dom", "month", "year", "tz"]
    for i, v in enumerate(regs):
        print(f"  {cfg.start_address + i} ({labels[i]}): {v}")
    return 0


def run_test(cfg: Config, provider: TimeProvider) -> int:
    # Filter enabled devices
    enabled_devices = [d for d in cfg.devices if d.enabled]
    if not enabled_devices:
        logging.error("No enabled devices found in configuration.")
        return 1

    dev = enabled_devices[0]
    start = _effective_start_address(cfg)

    logging.info("Mode=TEST: Only the first device will be updated: %s (unit_id=%d)", dev.ip, dev.unit_id)

    # Prepare target timestamp at the next :00 (if enabled)
    target = compute_target_time(provider, cfg.offset_seconds, cfg.align_to_next_second_zero)
    regs = build_register_values(target)

    # Connect
    try:
        client = _connect_client(dev.ip, dev.port or cfg.port, dev.timeout_s or cfg.timeout_s)
    except Exception as e:
        logging.error("Connection failed to %s:%d - %s", dev.ip, dev.port or cfg.port, e)
        return 1

    try:
        # Read initial
        init_vals = _read_registers(client, start, REG_COUNT, dev.unit_id)
        if init_vals is None:
            logging.warning("Initial read failed on %s (unit=%d)", dev.ip, dev.unit_id)
            # By requirement: if initial read fails in test, we abort write
            client.close()
            return 1

        logging.info("Initial PLC clock values @%s: %s", dev.ip, init_vals)
        print(f"TEST - Initial values from {dev.ip} (unit {dev.unit_id}): {init_vals}")
        print(f"TEST - Target values to write (start {cfg.start_address}, base {cfg.address_base}): {regs}")

        # Wait for next second == 0 if configured
        if cfg.align_to_next_second_zero:
            logging.info("Waiting for next second == 0...")
            wait_until_next_second_zero(provider)

        # Write
        ok = _write_registers(client, start, regs, dev.unit_id)
        if not ok:
            logging.error("Write failed on %s (unit=%d)", dev.ip, dev.unit_id)
            client.close()
            return 1

        # Verify (read-back)
        readback = _read_registers(client, start, REG_COUNT, dev.unit_id)
        logging.info("Read-back after write @%s: %s", dev.ip, readback)
        print(f"TEST - Read-back values from {dev.ip}: {readback}")

        if readback != regs:
            logging.warning("Read-back mismatch on %s (expected %s, got %s)", dev.ip, regs, readback)
            return 1

        logging.info("TEST completed successfully.")
        return 0

    finally:
        try:
            client.close()
        except Exception:
            pass


def run_normal(cfg: Config, provider: TimeProvider) -> int:
    devices = [d for d in cfg.devices if d.enabled]
    if not devices:
        logging.error("No enabled devices found in configuration.")
        return 1

    start = _effective_start_address(cfg)

    # Compute the target values for the *upcoming* minute boundary (if enabled).
    target = compute_target_time(provider, cfg.offset_seconds, cfg.align_to_next_second_zero)
    regs = build_register_values(target)

    # Align to next :00 before writing, if requested
    if cfg.align_to_next_second_zero:
        logging.info("Waiting for next second == 0 before bulk write...")
        wait_until_next_second_zero(provider)

    failures = 0
    for dev in devices:
        try:
            client = _connect_client(dev.ip, dev.port or cfg.port, dev.timeout_s or cfg.timeout_s)
        except Exception as e:
            logging.error("Connection failed to %s:%d - %s", dev.ip, dev.port or cfg.port, e)
            failures += 1
            continue

        try:
            ok = False
            for attempt in range(1 + cfg.retries):
                ok = _write_registers(client, start, regs, dev.unit_id)
                if ok:
                    break
                time.sleep(0.150)  # small backoff

            if not ok:
                logging.error("Write failed on %s (unit=%d) after retries", dev.ip, dev.unit_id)
                failures += 1
            else:
                if cfg.verify_after_write:
                    rb = _read_registers(client, start, REG_COUNT, dev.unit_id)
                    if rb != regs:
                        logging.warning("Verify mismatch on %s (expected %s, got %s)", dev.ip, regs, rb)
                        failures += 1

        finally:
            try:
                client.close()
            except Exception:
                pass

    if failures > 0:
        logging.error("Completed with %d failure(s) over %d device(s).", failures, len(devices))
        return 1
    logging.info("Completed successfully over %d device(s).", len(devices))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PLC clock sync over Modbus TCP")
    parser.add_argument("-c", "--config", required=True, help="Path to YAML/JSON config file")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Logging
    also_console = cfg.mode in ("debug", "test")
    setup_logging(cfg.log_file, cfg.log_level, also_console=also_console)
    logging.info("Starting clock sync | mode=%s | source_clock=%s | offset=%s",
                 cfg.mode, cfg.source_clock, cfg.offset_seconds)

    # Time provider
    try:
        provider = get_time_provider(cfg)
    except Exception as e:
        logging.error("Time provider initialization failed: %s", e)
        return 1

    try:
        if cfg.mode == "debug":
            return run_debug(cfg, provider)
        elif cfg.mode == "test":
            return run_test(cfg, provider)
        elif cfg.mode == "normal":
            return run_normal(cfg, provider)
        else:
            logging.error("Unknown mode: %s", cfg.mode)
            return 2
    finally:
        logging.info("Exiting.")


if __name__ == "__main__":
    sys.exit(main())