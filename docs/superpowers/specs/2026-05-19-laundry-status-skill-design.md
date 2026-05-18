# Laundry Status Skill Design

## Goal
Create an OpenClaw skill that lets users query washing machine and dryer status at Shanghai Jiao Tong University (Minhang campus) via natural language.

## Approach
**Minimal:** One Python script + one SKILL.md. No config files, no dependencies beyond Python stdlib + requests.

## File Structure
```
laundry-status/
  SKILL.md
  laundry_status.py
```

## SKILL.md
- **name**: `laundry-status`
- **description**: Use when the user wants to check washing machine or dryer status, laundry room availability, or which machines are free at SJTU Minhang campus.
- **triggers**: 洗衣机, 洗衣房, washer, dryer, laundry, 空闲机器, 有没有空的洗衣机
- **emoji**: 👕
- **tags**: iot, smart-home, laundry, haier, sjtu

Body tells OpenClaw to invoke `python3 laundry_status.py` (default) or `python3 laundry_status.py --building 西16楼`, parse output, and reply in the user's language.

## laundry_status.py

### Hardcoded config
```python
BUILDINGS = [
    {"id": "27131", "name": "西21楼"},
    {"id": "27136", "name": "西16楼"},
]
DEFAULT_BUILDING = "西21楼"
```

### CLI interface
- `python3 laundry_status.py` — query default building (西21楼)
- `python3 laundry_status.py --building 西16楼` — query by name
- `python3 laundry_status.py --building 27136` — query by ID
- `python3 laundry_status.py --all` — query all buildings

### API calls
- `POST https://yshz-user.haier-ioc.com/position/deviceDetailPage`
- categoryCode `00` = washers, `02` = dryers
- No auth required

### Output format
Text output grouped by device type, sorted by status:
```
西21楼 洗衣机状态：
  空闲: 3台（1号、5号、8号）
  使用中: 4台（2号 [预计14:30结束]、3号 [预计15:00结束]、...）
  故障: 1台（6号）

烘干机状态：
  空闲: 2台（1号、3号）
  使用中: 1台（2号 [预计14:15结束]）
```

### State mapping
- 1 → 空闲
- 2 → 使用中
- 3 → 故障
- 4 → 离线

## Error Handling
- Network timeout → return error message in Chinese
- API error (code != 0) → return API error message
- Unknown building name → list available buildings

## Testing
- Run script against live API and verify output
- Test with `--building`, `--all`, and default (no args)
- Test error case (invalid building name)
