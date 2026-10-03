from app.services.fill_engine import build_fill_lines, compute_gap, summarize

def test_gap_basic():
    assert compute_gap(20, 5, 0) == 15
    assert compute_gap(20, 10, 5) == 5

def test_no_negative_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 12, "in_transit": 0}]
    lines = build_fill_lines(lanes)
    assert lines[0].fill_qty == 0
    assert lines[0].status == "overbooked"

def test_cap_by_gap():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 20, "stock": 5, "in_transit": 0}]
    lines = build_fill_lines(lanes, requested={1: 100})
    assert lines[0].fill_qty == 15
    assert lines[0].gap == 15

def test_full_zero_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 8, "in_transit": 2}]
    s = summarize(build_fill_lines(lanes))
    assert s["full_count"] == 1
    assert s["total_fill"] == 0

def test_requested_cannot_inflate_overbooked_or_full():
    lanes = [
        {"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 12, "in_transit": 0},
        {"id": 2, "slot_no": "A2", "sku_name": "茶", "capacity": 10, "stock": 8, "in_transit": 2},
    ]
    lines = build_fill_lines(lanes, requested={1: 9, 2: 9})
    assert lines[0].fill_qty == 0 and lines[0].status == "overbooked"
    assert lines[1].fill_qty == 0 and lines[1].status == "full"
