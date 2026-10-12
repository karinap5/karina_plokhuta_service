from datetime import datetime,timedelta,timezone
from app.rules import overlaps, fits, utilization, cancelled_share
T=datetime(2026,10,1,8,tzinfo=timezone.utc)
def test_adjacent_intervals_do_not_overlap():
    assert not overlaps(T,T+timedelta(hours=1),T+timedelta(hours=1),T+timedelta(hours=2))
def test_contained_interval_overlaps():
    assert overlaps(T,T+timedelta(hours=1),T+timedelta(minutes=10),T+timedelta(minutes=20))
def test_service_fits_exactly():
    assert fits(T,60,T,T+timedelta(hours=1))
def test_too_long_service_does_not_fit():
    assert not fits(T,61,T,T+timedelta(hours=1))
def test_utilization_and_empty_schedule():
    assert utilization(30,120)==25
    assert utilization(0,0)==0
def test_cancelled_share_and_empty_period():
    assert cancelled_share(1,4)==25
    assert cancelled_share(0,0)==0
