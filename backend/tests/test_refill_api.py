"""钉死两条铁律：
1) 活口三处（补货生成 run / 满仓列表 full / 汇总计数 summary）永远同一套数字，且随库存一起变；
2) 历史补货单详情永远停在生成当时，改库存不得回刷旧单。
裁定只在 fill_engine 一处：任何绕过它的私自直算都必须与三页对账失败。
"""
from sqlalchemy import select
from app.models.models import Lane
from app.services.fill_engine import build_fill_lines, summarize

LOC = 1


def _lanes_payload(db):
    db.expire_all()
    lanes = db.scalars(select(Lane).where(Lane.location_id == LOC).order_by(Lane.slot_no)).all()
    return [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
             "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]


def _engine_view(db):
    """唯一裁定对当前库存的活算结果——三页必须等于它。"""
    return summarize(build_fill_lines(_lanes_payload(db)))


def _set_stock(db, slot_no, stock):
    lane = db.scalars(select(Lane).where(Lane.slot_no == slot_no)).one()
    lane.stock = stock
    db.commit()


def _assert_three_paths_agree(client, db):
    """活口三处互相对账，且与唯一裁定的活算结果一致。"""
    expected = _engine_view(db)
    run = client.post(f"/api/refills/run?location_id={LOC}").json()
    full = client.get(f"/api/refills/full?location_id={LOC}").json()
    summary = client.get(f"/api/refills/summary?location_id={LOC}").json()
    assert run["lines"] == expected["lines"]
    assert run["total_fill"] == expected["total_fill"]
    assert {l["lane_id"] for l in full["lanes"]} == \
           {l["lane_id"] for l in expected["lines"] if l["status"] == "full"}
    assert summary["total_fill"] == expected["total_fill"]
    assert summary["need_fill_count"] == expected["need_fill_count"]
    assert summary["full_count"] == expected["full_count"]
    assert summary["overbooked_count"] == expected["overbooked_count"]
    # 三处互认：汇总满仓数 == 满仓列表长度 == 生成单内 full 行数
    assert summary["full_count"] == len(full["lanes"])
    assert summary["full_count"] == sum(1 for l in run["lines"] if l["status"] == "full")
    assert summary["total_fill"] == run["total_fill"] == sum(l["fill_qty"] for l in run["lines"])
    return run, full, summary


def test_live_three_paths_agree_and_track_inventory(client, db):
    _, full1, summary1 = _assert_three_paths_agree(client, db)
    assert summary1["total_fill"] == 32
    assert {l["slot_no"] for l in full1["lanes"]} == {"A2", "B2"}

    # 改库存：A2 满仓→待补(缺8)，C1 待补→满仓
    _set_stock(db, "A2", 10)
    _set_stock(db, "C1", 10)

    _, full2, summary2 = _assert_three_paths_agree(client, db)
    # 活口三处一起跟新：总量 32→30，满仓集合 {A2,B2}→{B2,C1}
    assert summary2["total_fill"] == 30
    assert summary2["need_fill_count"] == 3
    assert {l["slot_no"] for l in full2["lanes"]} == {"B2", "C1"}


def test_historical_order_frozen_after_inventory_change(client, db):
    order1 = client.post(f"/api/refills/run?location_id={LOC}").json()
    assert order1["total_fill"] == 32

    _set_stock(db, "A2", 10)
    _set_stock(db, "C1", 10)

    # 旧单详情必须停在生成当时
    detail = client.get(f"/api/refills/{order1['id']}").json()
    assert detail["lines"] == order1["lines"]
    assert detail["total_fill"] == 32
    latest = client.get(f"/api/refills/latest?location_id={LOC}").json()
    assert latest["id"] == order1["id"] and latest["total_fill"] == 32

    # 而活口已经跟新库存走了（证明活口读的不是旧快照）
    assert client.get(f"/api/refills/summary?location_id={LOC}").json()["total_fill"] == 30

    # 再开新单，旧单依旧不被回刷
    order2 = client.post(f"/api/refills/run?location_id={LOC}").json()
    assert order2["total_fill"] == 30
    again = client.get(f"/api/refills/{order1['id']}").json()
    assert again["total_fill"] == 32
    assert again["lines"] == order1["lines"]
    assert client.get(f"/api/refills/latest?location_id={LOC}").json()["id"] == order2["id"]


def test_seeded_overbooked_excluded_from_full_and_zero_fill(client, db):
    full = client.get(f"/api/refills/full?location_id={LOC}").json()
    assert {l["slot_no"] for l in full["lanes"]} == {"A2", "B2"}  # 种子超占道 C2 不得进满仓
    assert all(l["gap"] == 0 for l in full["lanes"])

    summary = client.get(f"/api/refills/summary?location_id={LOC}").json()
    assert summary["overbooked_count"] == 1

    run = client.post(f"/api/refills/run?location_id={LOC}").json()
    c2 = next(l for l in run["lines"] if l["slot_no"] == "C2")
    assert c2["status"] == "overbooked"
    assert c2["fill_qty"] == 0  # 超占补量必 0
    assert c2["gap"] == -2


def test_forged_bypass_calculation_fails_reconciliation(client, db):
    """绕过裁定的私自直算（另写减法）与三页对账必然失败；唯一裁定与三页必然一致。"""
    run = client.post(f"/api/refills/run?location_id={LOC}").json()
    full = client.get(f"/api/refills/full?location_id={LOC}").json()
    summary = client.get(f"/api/refills/summary?location_id={LOC}").json()
    rows = _lanes_payload(db)
    api_full_slots = {l["slot_no"] for l in full["lanes"]}

    # 伪造甲：缺口私自写成 容量−库存（吞掉在途）→ 把超占 C2 算成满仓、总量算成 39
    assert {r["slot_no"] for r in rows if r["capacity"] - r["stock"] == 0} != api_full_slots
    assert sum(max(0, r["capacity"] - r["stock"]) for r in rows) != summary["total_fill"]

    # 伪造乙：补量用未钳制的裸缺口 → 超占道给出 −2，总量 30 ≠ 32
    assert sum(r["capacity"] - r["stock"] - r["in_transit"] for r in rows) != summary["total_fill"]

    # 伪造丙：超占混进满仓（gap<=0 即满仓）
    assert {r["slot_no"] for r in rows if r["capacity"] - r["stock"] - r["in_transit"] <= 0} != api_full_slots

    # 唯一裁定（fill_engine）与三页对账成功
    expected = _engine_view(db)
    assert run["lines"] == expected["lines"]
    assert summary["total_fill"] == expected["total_fill"]
    assert api_full_slots == {l["slot_no"] for l in expected["lines"] if l["status"] == "full"}


def test_orders_index_detail_and_404(client, db):
    assert client.get(f"/api/refills/latest?location_id={LOC}").status_code == 404

    run = client.post(f"/api/refills/run?location_id={LOC}").json()
    orders = client.get(f"/api/refills/orders?location_id={LOC}").json()
    assert [o["id"] for o in orders] == [run["id"]]

    detail = client.get(f"/api/refills/{run['id']}").json()
    assert detail["lines"] == run["lines"]
    assert detail["total_fill"] == run["total_fill"]
    assert "created_at" in detail

    assert client.get("/api/refills/999999").status_code == 404
    assert client.get("/api/refills/summary?location_id=999999").status_code == 404
    assert client.get("/api/refills/full?location_id=999999").status_code == 404
