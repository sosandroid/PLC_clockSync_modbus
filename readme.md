# PLC Clock Synchronization over Modbus TCP

[Version française](./readme_fr.md)

This tool updates PLC clocks using **Modbus TCP (Function Code 16)**. It has been done for Crouzet's PLC to align their internal RTC to a time reference.  
It writes 8 contiguous **holding registers** describing the clock:

| Register (decimal) | Meaning                      | Range        |
|--------------------|------------------------------|--------------|
| 55                 | Seconds                       | 0..59        |
| 56                 | Minutes                       | 0..59        |
| 57                 | Hours                         | 0..23        |
| 58                 | Day of week (Mon=0..Sun=6)    | 0..6         |
| 59                 | Day of month                  | 1..31        |
| 60                 | Month                         | 1..12        |
| 61                 | Year (two digits)             | 0..99        |
| 62                 | Timezone offset (hours)       | -12..+12     |

> The timezone is computed from the local timezone **including DST**.

The script can use either the **system clock** or an **NTP (SNTP) time source**, without modifying the host clock.  
It can optionally **align the write at the next `second == 0`** to minimize drift across devices.

[![Buy me a coffee](./res/default-yellow.png)](https://www.buymeacoffee.com/ju9hJ8RqGk)

> Code provided as sample which must be tested before use in production environment. Feel free to modify it for a perfect requirements match 
---
## Flowchart -  How it works
````mermaid
flowchart LR
    A[Time reference] -->|ntp or local| B(This .py script)
    B -->|Modbus TCP| D[PLC 1]
    B -->|Modbus TCP| E[PLC 2]
    B -->|Modbus TCP| F[PLC 3]
````
## Features

- Config-driven (YAML or JSON).
- **Unit ID per device**, including **Unit ID = 0** for PLCs that require it.
- Address base parameter: if your documentation uses base-1 addresses (e.g., 40001-style), set `address_base: 1`.
- 3 modes:
  - **debug**: compute and print registers, **no write**.
  - **test**: read-before / write / read-after on **the first enabled device** only.
  - **normal**: write to **all enabled devices** (optional verify).
- **No change to system clock.** NTP is only used as a reference.
- Logging to a configurable file (default `./clock-sync.log`).

---

## Installation

### Prerequisites

- **Python 3.9+** (tested with 3.9–3.12)
- [pymodbus](https://pymodbus.readthedocs.io/en/latest/)
- [pyyaml](https://pypi.org/project/PyYAML/)
- Network access to PLCs (TCP 502) and (optionally) to NTP servers (UDP 123)

### Install dependencies

```bash
pip install pymodbus pyyaml
````

Run from a console with network access to PLCs.
No root rights are needed (the script does not set the host time).
Ensure outbound UDP 123 and TCP 502 are permitted.

## Usage

Usage
````bash
python clock_sync.py --config config.yaml
````

### Modes

- Debug : Prints the computed register table; no Modbus communication.
- Test : Reads the initial values, writes the new time on the first enabled device only, then reads back and prints the result.
- Normal : Writes to all enabled devices. Console stays quiet; see the log file for results.

## How the time is computed

- The tool picks the time source:
    - system: current local time (datetime.now().astimezone()).
    - ntp: SNTP query (48-byte request) to the first server; the second server is a fallback. The host clock is not modified.
- If align_to_next_second_zero=true, the target time is the next minute boundary (second=0). Otherwise, the target is the current time.
- The offset_seconds is applied to the target time.
- The timezone hours are derived from the target time’s local offset, including DST.
- The eight registers are written in one Write Multiple Registers (FC16) operation.

## Automation
The script can be called from a cronjob or Window's scheduled tasks. Running it every week is a good timeframe. Every day for those wanting a better precision.
