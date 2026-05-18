# Laundry Status Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ []`) syntax for tracking.

**Goal:** Create an OpenClaw skill with a Python script and SKILL.md that queries Haier IoT API for SJTU laundry machine status.

**Architecture:** Single Python script calls Haier API, outputs formatted text. SKILL.md tells OpenClaw how and when to invoke it.

**Tech Stack:** Python 3 (stdlib + requests)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `laundry-status/laundry_status.py` | CLI script: parse args, call API, format output |
| `laundry-status/SKILL.md` | OpenClaw skill definition: triggers, instructions |

---

### Task 1: Create the query script `laundry_status.py`

**Files:**
- Create: `laundry-status/laundry_status.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Query SJTU laundry machine status via Haier IoT API."""

import argparse
import sys
import requests

API_URL = "https://yshz-user.haier-ioc.com/position/deviceDetailPage"

BUILDINGS = [
    {"id": "27131", "name": "西21楼"},
    {"id": "27136", "name": "西16楼"},
]

DEFAULT_BUILDING = "西21楼"


def find_building(name_or_id):
    for b in BUILDINGS:
        if name_or_id in (b["name"], b["id"]):
            return b
    return None


def fetch_devices(position_id, category_code="00"):
    body = {
        "positionId": position_id,
        "categoryCode": category_code,
        "page": 1,
        "floorCode": "",
        "pageSize": 20,
    }
    resp = requests.post(API_URL, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(f"API error: {data['message']}")
    return data["data"]["items"]


STATE_MAP = {1: "空闲", 2: "使用中", 3: "故障", 4: "离线"}


def format_devices(devices, label):
    groups = {}
    for d in devices:
        state = d["state"]
        groups.setdefault(state, []).append(d)

    lines = [f"  {label}:"]
    if not devices:
        lines.append("    （无设备）")
        return "\n".join(lines)

    for state in sorted(groups.keys()):
        state_label = STATE_MAP.get(state, f"未知({state})")
        devs = groups[state]
        parts = []
        for d in sorted(devs, key=lambda x: x["name"]):
            name = d["name"]
            if state == 2 and d.get("finishTime"):
                time_str = d["finishTime"].split(" ")[1][:5]
                parts.append(f"{name} [预计{time_str}结束]")
            else:
                parts.append(name)
        lines.append(f"    {state_label}: {len(devs)}台（{'、'.join(parts)}）")
    return "\n".join(lines)


def query_building(building):
    position_id = building["id"]
    name = building["name"]
    try:
        washers = fetch_devices(position_id, "00")
        dryers = fetch_devices(position_id, "02")
    except requests.RequestException as e:
        return f"{name} 查询失败：网络错误 - {e}"
    except RuntimeError as e:
        return f"{name} 查询失败：{e}"

    parts = [f"{name}："]
    parts.append(format_devices(washers, "洗衣机"))
    if dryers:
        parts.append(format_devices(dryers, "烘干机"))
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="查询洗衣机/烘干机状态")
    parser.add_argument("--building", "-b", default=DEFAULT_BUILDING,
                        help="楼栋名称或ID（默认：西21楼）")
    parser.add_argument("--all", "-a", action="store_true",
                        help="查询所有楼栋")
    args = parser.parse_args()

    if args.all:
        for b in BUILDINGS:
            print(query_building(b))
            print()
    else:
        building = find_building(args.building)
        if not building:
            available = "、".join(b["name"] for b in BUILDINGS)
            print(f"未找到楼栋「{args.building}」，可选：{available}")
            sys.exit(1)
        print(query_building(building))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test the script with live API**

Run: `python3 laundry-status/laundry_status.py`
Expected: formatted output showing washer/dryer status for 西21楼

Run: `python3 laundry-status/laundry_status.py --building 西16楼`
Expected: formatted output for 西16楼

Run: `python3 laundry-status/laundry_status.py --all`
Expected: output for both buildings

Run: `python3 laundry-status/laundry_status.py --building 不存在`
Expected: error message listing available buildings

- [ ] **Step 3: Commit**

```bash
git add laundry-status/laundry_status.py
git commit -m "feat: add laundry status query script for SJTU"
```

---

### Task 2: Create SKILL.md

**Files:**
- Create: `laundry-status/SKILL.md`

- [ ] **Step 1: Write SKILL.md**

```markdown
---
name: laundry-status
description: Use when the user wants to check washing machine or dryer status, laundry room availability, or which machines are free at Shanghai Jiao Tong University Minhang campus.
version: 1.0.0
metadata:
  openclaw:
    emoji: "👕"
    triggers:
      - 洗衣机
      - 洗衣房
      - washer
      - dryer
      - laundry
      - 空闲机器
      - 有没有空的
      - 洗衣机状态
      - 楼下洗衣机
    tags:
      - iot
      - smart-home
      - laundry
      - haier
      - sjtu
---

# 洗衣机状态查询 (Laundry Status)

## Overview

Query real-time washing machine and dryer status at SJTU Minhang campus via Haier IoT API. Supports two buildings (西21楼, 西16楼).

## When to Use

Trigger when the user asks about:
- Washing machine or dryer availability ("洗衣机有没有空的？")
- Laundry room status ("洗衣房什么情况？")
- Which machines are free ("哪台洗衣机空着？")
- Machine finish time ("洗衣机什么时候结束？")

## How to Use

### Default query (西21楼)
```bash
python3 {skill_dir}/laundry_status.py
```

### Query specific building
```bash
python3 {skill_dir}/laundry_status.py --building 西16楼
```

### Query all buildings
```bash
python3 {skill_dir}/laundry_status.py --all
```

## Output Interpretation

The script outputs grouped results by device type (洗衣机/烘干机), with status per machine:

- **空闲** — Available, ready to use
- **使用中** — Running, with estimated finish time in brackets
- **故障** — Out of order
- **离线** — Offline

Present the results to the user in their language. Highlight idle machines if they want to do laundry. Mention estimated finish times for running machines.

## Available Buildings

| Name | ID |
|------|----|
| 西21楼 | 27131 |
| 西16楼 | 27136 |

## Error Handling

- Network errors: tell the user to check their connection
- Unknown building: suggest available building names
- API errors: report the error message from API
```

- [ ] **Step 2: Review SKILL.md for correctness**

Verify:
- YAML frontmatter fields are valid
- `{skill_dir}` placeholder will be resolved by OpenClaw at runtime
- Triggers cover common user phrases in Chinese and English
- Commands use correct relative path

- [ ] **Step 3: Test script invocation from SKILL.md commands**

Run: `python3 laundry-status/laundry_status.py`
Run: `python3 laundry-status/laundry_status.py --building 西16楼`
Run: `python3 laundry-status/laundry_status.py --all`

Expected: all three produce correct formatted output

- [ ] **Step 4: Commit**

```bash
git add laundry-status/SKILL.md
git commit -m "feat: add OpenClaw skill definition for laundry status"
```

---

### Task 3: Final verification

**Files:**
- Read: `laundry-status/laundry_status.py`
- Read: `laundry-status/SKILL.md`

- [ ] **Step 1: Review both files together**

Check:
- SKILL.md commands reference the correct script path
- All spec requirements are met: two buildings, default 西21楼, switchable, status grouping, finish times
- Error handling covers network errors, API errors, unknown building
- No TODO/TBD placeholders

- [ ] **Step 2: Full end-to-end test**

Run: `python3 laundry-status/laundry_status.py`
Run: `python3 laundry-status/laundry_status.py -b 西16楼`
Run: `python3 laundry-status/laundry_status.py --all`
Run: `python3 laundry-status/laundry_status.py -b 不存在`

Expected: all four commands produce correct output (three success, one error)

- [ ] **Step 3: Mark complete**

Done. Skill is ready to install in OpenClaw via:
```bash
# Manual install:
cp -r laundry-status ~/.openclaw/skills/
# Or tell OpenClaw to install from the repo directly.
```
