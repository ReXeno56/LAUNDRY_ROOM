from datetime import datetime, timedelta, timezone
import hashlib


def escape_text(value):
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def fold_line(line):
    lines, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            lines.append(current)
            current = " "
        current += char
    lines.append(current)
    return "\r\n".join(lines)


def make_calendar(name, finish, minutes):
    remind = finish - timedelta(minutes=minutes)
    stamp = lambda dt: dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    uid = hashlib.sha256(f"{name}:{finish.timestamp()}:{minutes}".encode()).hexdigest()[:32]
    description = escape_text(f"预计 {finish.strftime('%H:%M')} 结束，提前{minutes}分钟提醒你去检查")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Laundry//CN", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT", f"UID:{uid}@laundry", f"DTSTAMP:{stamp(datetime.now(timezone.utc))}",
        f"DTSTART:{stamp(remind)}", f"DTEND:{stamp(finish)}",
        f"SUMMARY:{escape_text(name)} 快空了", f"DESCRIPTION:{description}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:PT0S", f"DESCRIPTION:{description}",
        "END:VALARM", "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(fold_line(line) for line in lines) + "\r\n"
