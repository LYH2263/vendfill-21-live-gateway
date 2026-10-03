import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import live_snapshot, select_full

router = APIRouter(prefix="/refills", tags=["refills"])


def _current_lanes(db: Session, location_id: int) -> list[dict]:
    """读当前库存。活口三路径只准经此取数，再交 fill_engine 裁定。"""
    if db.get(Location, location_id) is None:
        raise HTTPException(404, "点位不存在")
    rows = db.scalars(
        select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)
    ).all()
    return [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
             "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit}
            for l in rows]


def _live(db: Session, location_id: int) -> dict:
    # 三条活路径（run/full/summary）共同唯一入口：同一套现库存、同一处裁定。
    return live_snapshot(_current_lanes(db, location_id))


def _freeze(order: RefillOrder) -> dict:
    """历史单详情：原样回读生成当时的冻结快照，绝不碰现库存、绝不重算。"""
    data = json.loads(order.lines_json)
    return {"id": order.id, "location_id": order.location_id,
            "created_at": order.created_at.isoformat(), **data}


@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    snapshot = _live(db, location_id)  # 活算
    order = RefillOrder(location_id=location_id,
                        lines_json=json.dumps(snapshot, ensure_ascii=False))
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"id": order.id, "location_id": location_id, **snapshot}


@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    snapshot = _live(db, location_id)  # 活算：满仓随当前库存变化
    return {
        "location_id": location_id,
        "lanes": select_full(snapshot["lines"]),  # 超占不具 full 身份，必被排除
        "total_fill": snapshot["total_fill"],
        "need_fill_count": snapshot["need_fill_count"],
        "full_count": snapshot["full_count"],
        "overbooked_count": snapshot["overbooked_count"],
    }


@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    snapshot = _live(db, location_id)  # 活算：计数随当前库存变化
    return {
        "location_id": location_id,
        "total_fill": snapshot["total_fill"],
        "need_fill_count": snapshot["need_fill_count"],
        "full_count": snapshot["full_count"],
        "overbooked_count": snapshot["overbooked_count"],
    }


@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    # 最近一张历史单的冻结详情；无单时显式 404，绝不隐式活算生成。
    order = db.scalars(
        select(RefillOrder).where(RefillOrder.location_id == location_id)
        .order_by(RefillOrder.id.desc())
    ).first()
    if order is None:
        raise HTTPException(404, "尚无补货单")
    return _freeze(order)


@router.get("/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RefillOrder, order_id)
    if order is None:
        raise HTTPException(404, "补货单不存在")
    return _freeze(order)
