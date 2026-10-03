"""补货唯一裁定逻辑。

铁律：缺口（gap）、状态（status）、补量（fill_qty）只许在本模块裁定。
- compute_gap 是全系统唯一允许的缺口减法（capacity - stock - in_transit）；
- adjudicate 是状态与补量的唯一来源（超占补量必 0、超占不得进满仓）；
- 三条活路径（补货生成 /run、满仓列表 /full、汇总计数 /summary）
  与历史冻结详情，都只能调用这里的产出，不得私自另写减法。

活口：live_snapshot 读当前库存 → 现算现变。
历史：生成当时把 snapshot 落库，详情只原样回读，禁止重算。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

STATUS_NEED_FILL = "need_fill"
STATUS_FULL = "full"
STATUS_OVERBOOKED = "overbooked"


@dataclass
class FillLine:
    lane_id: int
    slot_no: str
    sku_name: str
    capacity: int
    stock: int
    in_transit: int
    gap: int
    fill_qty: int
    status: str  # need_fill | full | overbooked


def compute_gap(capacity: int, stock: int, in_transit: int) -> int:
    """全系统唯一的缺口减法。任何路径不得自行 capacity - stock (- in_transit)。"""
    return int(capacity) - int(stock) - int(in_transit)


def adjudicate(capacity: int, stock: int, in_transit: int,
               desired: int | None = None) -> tuple[int, int, str]:
    """裁定 (gap, fill_qty, status)，唯一来源。

    - gap < 0 超占：补量必 0，状态 overbooked（不得进满仓）；
    - gap == 0 满仓：补量 0，状态 full；
    - gap > 0 待补：补量夹在 [0, gap]。
    """
    gap = compute_gap(capacity, stock, in_transit)
    if gap < 0:
        return gap, 0, STATUS_OVERBOOKED
    if gap == 0:
        return gap, 0, STATUS_FULL
    want = gap if desired is None else int(desired)
    return gap, max(0, min(want, gap)), STATUS_NEED_FILL


def build_fill_lines(lanes: list[dict], requested: dict[int, int] | None = None) -> list[FillLine]:
    """读当前货道库存活算。requested 为期望补量，一律被缺口裁定夹断。"""
    lines: list[FillLine] = []
    for lane in lanes:
        desire = None if requested is None else requested.get(lane["id"])
        gap, fill, status = adjudicate(
            lane["capacity"], lane["stock"], lane["in_transit"], desire
        )
        lines.append(FillLine(
            lane_id=lane["id"], slot_no=lane["slot_no"], sku_name=lane["sku_name"],
            capacity=lane["capacity"], stock=lane["stock"], in_transit=lane["in_transit"],
            gap=gap, fill_qty=fill, status=status,
        ))
    return lines


def line_to_dict(line: FillLine | dict) -> dict:
    return asdict(line) if isinstance(line, FillLine) else dict(line)


def lines_to_dicts(lines: list[FillLine | dict]) -> list[dict]:
    return [line_to_dict(l) for l in lines]


def select_full(lines: list[FillLine | dict]) -> list[dict]:
    """满仓列表唯一筛选口：只认 status == full，超占（gap<0）天然排除。"""
    rows = [line_to_dict(l) for l in lines]
    return [l for l in rows if l["status"] == STATUS_FULL]


def summarize(lines: list[FillLine | dict]) -> dict:
    """汇总计数唯一来源：只对同一份裁定行集合点数，不另算任何缺口。"""
    rows = lines_to_dicts(lines)
    return {
        "total_fill": sum(l["fill_qty"] for l in rows),
        "need_fill_count": sum(1 for l in rows if l["status"] == STATUS_NEED_FILL),
        "full_count": sum(1 for l in rows if l["status"] == STATUS_FULL),
        "overbooked_count": sum(1 for l in rows if l["status"] == STATUS_OVERBOOKED),
        "lines": rows,
    }


def live_snapshot(lanes: list[dict], requested: dict[int, int] | None = None) -> dict:
    """三条活路径共同的唯一入口：现库存 → 裁定 → 汇总，一次产出，三口同数。"""
    return summarize(build_fill_lines(lanes, requested=requested))
