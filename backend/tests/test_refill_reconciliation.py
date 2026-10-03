"""钉死三条铁律：

1. 补货生成 /run、满仓 /full、汇总 /summary 三处数字永远同一套，
   改库存后三口一起变；
2. 历史补货单详情（/refills/{id}、/refills/latest）永远停在生成当时，
   禁止活算重算、不被后续库存变化回刷；
3. 缺口/状态只有一处裁定（fill_engine）：超占补量必 0、超占不进满仓。
   任何私自另写减法的「直算」路由，与三页对账必然失败。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.main import app
from app.models.models import Lane, Location
from app.services.seed import seed_if_empty


def _make_location(db: Session) -> int:
    loc = Location(code="T-01", name="测试点位")
    db.add(loc)
    db.flush()
    return loc.id


def _add_lane(db, loc_id, slot, sku, cap, stock, transit):
    db.add(Lane(location_id=loc_id, slot_no=slot, sku_name=sku,
                capacity=cap, stock=stock, in_transit=transit))
    db.flush()


def _by_slot(payload):
    return {l["slot_no"]: l for l in payload["lines"]}


# 三条活路径必须对得上的计数口径
COUNT_KEYS = ["total_fill", "need_fill_count", "full_count", "overbooked_count"]


def _assert_three_pages_agree(run_body, full_body, summary_body):
    for k in COUNT_KEYS:
        assert full_body[k] == run_body[k] == summary_body[k], f"三口在 {k} 上分叉"
    # 满仓页列出的行数必须等于汇总/生成口径里的满仓计数
    assert len(full_body["lanes"]) == run_body["full_count"] == summary_body["full_count"]
    run_full_slots = {l["slot_no"] for l in run_body["lines"] if l["status"] == "full"}
    assert {l["slot_no"] for l in full_body["lanes"]} == run_full_slots


def test_live_three_pages_move_together_and_old_order_frozen(client, session_factory):
    db = session_factory()
    loc_id = _make_location(db)
    # A1 待补(缺15) · A2 满仓(缺0) · B1 超占(缺 -2)
    _add_lane(db, loc_id, "A1", "矿泉水", 20, 5, 0)
    _add_lane(db, loc_id, "A2", "可乐", 18, 18, 0)
    _add_lane(db, loc_id, "B1", "口香糖", 24, 24, 2)
    db.commit()
    lane_b1 = db.scalar(select(Lane).where(Lane.slot_no == "B1"))
    assert lane_b1 is not None
    db.close()

    # —— 第一次活口生成 ——
    run1 = client.post(f"/api/refills/run?location_id={loc_id}").json()
    assert run1["total_fill"] == 15
    assert (run1["need_fill_count"], run1["full_count"], run1["overbooked_count"]) == (1, 1, 1)
    l1 = _by_slot(run1)
    assert l1["A1"]["status"] == "need_fill" and l1["A1"]["fill_qty"] == 15 and l1["A1"]["gap"] == 15
    assert l1["A2"]["status"] == "full" and l1["A2"]["fill_qty"] == 0
    # 超占：补量必 0、gap 为负、状态 overbooked
    assert l1["B1"]["status"] == "overbooked" and l1["B1"]["fill_qty"] == 0 and l1["B1"]["gap"] == -2

    full1 = client.get(f"/api/refills/full?location_id={loc_id}").json()
    summ1 = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    # 三口同数；超占道 B1 不得进满仓
    _assert_three_pages_agree(run1, full1, summ1)
    assert {l["slot_no"] for l in full1["lanes"]} == {"A2"}

    old_id = run1["id"]

    # —— 改库存：A2 卖掉 1 个（满仓→待补），不重新生成补货单 ——
    db = session_factory()
    a2 = db.scalar(select(Lane).where(Lane.slot_no == "A2"))
    a2.stock = 17
    db.commit()
    db.close()

    # 活口满仓与汇总必须立刻跟新，且三口仍是一套
    full2 = client.get(f"/api/refills/full?location_id={loc_id}").json()
    summ2 = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    assert (summ2["need_fill_count"], summ2["full_count"],
            summ2["overbooked_count"], summ2["total_fill"]) == (2, 0, 1, 16)
    assert full2["lanes"] == []  # A2 已转待补；B1 超占依旧不许混入
    for k in COUNT_KEYS:
        assert full2[k] == summ2[k]

    # —— 旧单详情必须停在生成当时：库存回刷不到它 ——
    detail = client.get(f"/api/refills/{old_id}").json()
    latest = client.get(f"/api/refills/latest?location_id={loc_id}").json()
    for body in (detail, latest):
        assert body["id"] == old_id
        assert (body["need_fill_count"], body["full_count"],
                body["overbooked_count"], body["total_fill"]) == (1, 1, 1, 15)
        frozen = _by_slot(body)
        assert frozen["A2"]["stock"] == 18 and frozen["A2"]["status"] == "full"
        assert frozen["B1"]["status"] == "overbooked" and frozen["B1"]["fill_qty"] == 0

    # 再生成一张新单：新单跟新库存，旧单纹丝不动
    run2 = client.post(f"/api/refills/run?location_id={loc_id}").json()
    assert run2["id"] != old_id
    assert (run2["need_fill_count"], run2["full_count"], run2["overbooked_count"]) == (2, 0, 1)
    detail_again = client.get(f"/api/refills/{old_id}").json()
    assert detail_again["total_fill"] == 15
    assert _by_slot(detail_again)["A2"]["stock"] == 18


def test_seed_overbooked_excluded_from_live_full_and_old_order_not_refreshed(client, session_factory):
    """对齐种子数据：C2(24/24/2) 超占，活口满仓永远排除它；改库存不回刷旧单。"""
    db = session_factory()
    seed_if_empty(db)
    loc = db.scalar(select(Location).where(Location.code == "VM-01"))
    loc_id = loc.id
    db.close()

    run = client.post(f"/api/refills/run?location_id={loc_id}").json()
    full = client.get(f"/api/refills/full?location_id={loc_id}").json()
    summ = client.get(f"/api/refills/summary?location_id={loc_id}").json()
    _assert_three_pages_agree(run, full, summ)
    assert "C2" not in {l["slot_no"] for l in full["lanes"]}  # 种子超占道排除
    c2 = _by_slot(run)["C2"]
    assert c2["status"] == "overbooked" and c2["gap"] == -2 and c2["fill_qty"] == 0
    old_id = run["id"]
    old_json = run["lines"]

    # 改动库存（A2 满仓货道卖空一件）后，旧单快照逐行不变
    db = session_factory()
    db.scalar(select(Lane).where(Lane.slot_no == "A2")).stock = 17
    db.commit()
    db.close()
    frozen = client.get(f"/api/refills/{old_id}").json()["lines"]
    assert frozen == old_json


# —— 伪造一个绕过 fill_engine 裁定、私自做减法的「直算」路由 ——
rogue = APIRouter(prefix="/rogue", tags=["rogue"])


@rogue.get("/refill-view")
def rogue_view(location_id: int = 1, db: Session = Depends(get_db)):
    # 故意违规：缺口漏掉在途；满仓判定用 <=0，让超占混进满仓；计数自己另点。
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id)).all()
    rows = []
    for l in lanes:
        gap = l.capacity - l.stock  # 私自减法：丢掉 in_transit
        status = "full" if gap <= 0 else "need_fill"  # 超占(gap<0)被塞进满仓
        rows.append({"slot_no": l.slot_no, "gap": gap, "status": status,
                     "fill_qty": max(0, gap)})
    return {
        "total_fill": sum(r["fill_qty"] for r in rows),
        "need_fill_count": sum(1 for r in rows if r["status"] == "need_fill"),
        "full_count": sum(1 for r in rows if r["status"] == "full"),
        "overbooked_count": 0,
        "full_slots": [r["slot_no"] for r in rows if r["status"] == "full"],
    }


app.include_router(rogue, prefix="/api")


def test_bypass_direct_calc_fails_reconciliation(client, session_factory):
    db = session_factory()
    loc_id = _make_location(db)
    _add_lane(db, loc_id, "A1", "矿泉水", 20, 5, 2)   # 真缺口 13，直算误成 15
    _add_lane(db, loc_id, "A2", "可乐", 18, 18, 0)   # 真满仓
    _add_lane(db, loc_id, "B1", "口香糖", 24, 24, 2)  # 真超占，直算会塞进满仓
    db.commit()
    db.close()

    canonical_run = client.post(f"/api/refills/run?location_id={loc_id}").json()
    canonical_full = client.get(f"/api/refills/full?location_id={loc_id}").json()
    canonical_summ = client.get(f"/api/refills/summary?location_id={loc_id}").json()

    rogue_body = client.get(f"/api/rogue/refill-view?location_id={loc_id}").json()

    # 与三页对账：任一口径对不上即判定失败
    mismatches = [
        k for k in COUNT_KEYS
        if not (rogue_body[k] == canonical_run[k] == canonical_full[k] == canonical_summ[k])
    ]
    assert mismatches, "绕过裁定的直算竟然与三页完全对账成功，裁定唯一性已被破坏"
    # 最刺眼的分叉：超占道混进满仓；补量与超占计数错误
    assert "B1" in rogue_body["full_slots"]
    assert "B1" not in {l["slot_no"] for l in canonical_full["lanes"]}
    assert rogue_body["total_fill"] != canonical_run["total_fill"]
    assert rogue_body["overbooked_count"] != canonical_summ["overbooked_count"]
