from datetime import timedelta

def overlaps(start, end, other_start, other_end):
    return start < other_end and other_start < end

def fits(start, minutes, slot_start, slot_end):
    return minutes > 0 and slot_start <= start and start + timedelta(minutes=minutes) <= slot_end

def utilization(booked_minutes, available_minutes):
    return round(100 * booked_minutes / available_minutes, 2) if available_minutes else 0.0

def cancelled_share(cancelled, total):
    return round(100 * cancelled / total, 2) if total else 0.0
