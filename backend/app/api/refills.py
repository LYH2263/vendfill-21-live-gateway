import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import build_fill_lines, summarize
router = APIRouter(prefix="/refills", tags=["refills"])

def _live_summary(db: Session, location_id: int) -> dict:
    """活口唯一来源：run / full / summary 三条活路径都调这里，缺口与状态的裁定只在 fill_engine。"""
    loc = db.get(Location, location_id)
    if not loc: raise HTTPException(404, "点位不存在")
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]
    return summarize(build_fill_lines(payload))

def _frozen(order: RefillOrder) -> dict:
    """历史补货单：只读生成当时冻结的快照，永不重算。"""
    return {"id": order.id, "location_id": order.location_id,
            "created_at": order.created_at.isoformat(), **json.loads(order.lines_json)}

@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    summary = _live_summary(db, location_id)
    order = RefillOrder(location_id=location_id, created_at=datetime.utcnow(),
                        lines_json=json.dumps(summary, ensure_ascii=False))
    db.add(order); db.commit(); db.refresh(order)
    return {"id": order.id, "location_id": location_id, **summary}

@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    data = _live_summary(db, location_id)
    return {"location_id": location_id, "lanes": [l for l in data["lines"] if l["status"] == "full"]}

@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    data = _live_summary(db, location_id)
    return {
        "location_id": location_id,
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
    }

@router.get("/orders")
def list_orders(location_id: int = 1, db: Session = Depends(get_db)):
    rows = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                      .order_by(RefillOrder.id.desc())).all()
    return [{"id": o.id, "location_id": o.location_id, "created_at": o.created_at.isoformat()} for o in rows]

@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    order = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                       .order_by(RefillOrder.id.desc())).first()
    if not order: raise HTTPException(404, "暂无补货单")
    return _frozen(order)

@router.get("/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db)):
    order = db.get(RefillOrder, order_id)
    if not order: raise HTTPException(404, "补货单不存在")
    return _frozen(order)
