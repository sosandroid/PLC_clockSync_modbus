# PLC Clock Synchronization over Modbus TCP

This tool updates PLC clocks using **Modbus TCP (Function Code 16 – Write Multiple Registers)**.  
It writes 8 contiguous **holding registers** (16-bit integers) describing the clock:

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

> All values are **binary 16-bit** (no BCD).  
> The timezone is computed from the local timezone **including DST**.

The script can use either the **system clock** or an **NTP (SNTP) time source**, without modifying the host clock.  
It can optionally **align the write at the next `second == 0`** to minimize drift across devices.

---

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
- Network access to PLCs (TCP 502) and (optionally) to NTP servers (UDP 123)

### Install dependencies

```bash
pip install pymodbus pyyaml