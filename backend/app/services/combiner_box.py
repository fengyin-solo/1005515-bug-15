"""汇流箱检测业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "combiner_box"
DEFECT_MODULE = "defect"

# 登记时必须齐全的字段：缺一即打回并写明原因，不许静默丢弃
REQUIRED_FIELDS = ["汇流箱编号", "所属阵列", "输入路数", "熔断器状态", "防雷模块状态", "通讯状态"]
# 登记时完整落库的字段：箱体温度、投运日期允许后补，但提交了就必须存住
PERSISTED_FIELDS = REQUIRED_FIELDS + ["箱体温度", "投运日期"]

STATUS_ORDER = ["运行正常", "熔断器异常", "通讯中断", "已停用"]
ACTION_RULES = {"恢复正常": "运行正常", "标记异常": "熔断器异常", "标记中断": "通讯中断", "停用设备": "已停用"}
NEGATIVE_ACTIONS = ["停用设备"]
# 进入这些状态要落缺陷单到处置清单
ABNORMAL_STATUSES = {"熔断器异常", "通讯中断"}
# 各运行状态下三个分项状态的回填口径（箱体温度不在内，沿用原有采集口径）
STATUS_FIELD_DEFAULTS = {
    "运行正常": ("正常", "正常", "正常"),
    "熔断器异常": ("异常", "正常", "正常"),
    "通讯中断": ("正常", "正常", "中断"),
    "已停用": ("停用", "停用", "停用"),
}
# 缺陷单口径：处理方案、严重等级、整改时限（天）
DEFECT_PLANS = {
    "熔断器异常": ("现场检查并更换熔断器，复核输入路数接线", "一般", 7),
    "通讯中断": ("排查通讯回路与模块供电，恢复箱体通讯", "严重", 3),
}


def _backfill_legacy_rows() -> None:
    """存量记录按投运日期回填：补齐缺失的运行状态与分项状态，重复编号只留最早一条。

    箱体温度不在回填范围内，过往数据沿用原有的采集口径。
    """
    rows = store.rows(MODULE)
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: int(r.get("id", 0))):
        code = str(row.get("汇流箱编号") or "").strip()
        if code and code in seen:
            continue  # 同一台汇流箱重复登记，只留最早一条
        if code:
            seen.add(code)
        kept.append(row)
    rows[:] = kept
    for row in sorted(kept, key=lambda r: str(r.get("投运日期") or "9999-12-31")):
        status = row.get("status") if row.get("status") in STATUS_ORDER else STATUS_ORDER[0]
        row["status"] = status
        if row.get("运行状态") not in STATUS_ORDER:
            row["运行状态"] = status
        fuse, surge, comm = STATUS_FIELD_DEFAULTS[status]
        for field, default in (("熔断器状态", fuse), ("防雷模块状态", surge), ("通讯状态", comm)):
            if not str(row.get(field) or "").strip():
                row[field] = default


def _sync_status_fields(entry: dict[str, Any], target: str) -> None:
    """分项状态跟着运行状态走，保证列表、详情、导出看到的是同一份口径。"""
    fuse, surge, comm = STATUS_FIELD_DEFAULTS[target]
    if target == "运行正常":
        entry["熔断器状态"] = fuse
        entry["通讯状态"] = comm
    elif target == "熔断器异常":
        entry["熔断器状态"] = fuse
        entry["通讯状态"] = comm
    elif target == "通讯中断":
        entry["通讯状态"] = comm
    elif target == "已停用":
        entry["熔断器状态"] = fuse
        entry["防雷模块状态"] = surge
        entry["通讯状态"] = comm


def _file_defect(entry: dict[str, Any], category: str) -> dict[str, Any]:
    """把处置结论落成一条缺陷单，进入缺陷管理清单。"""
    rows = store.rows(DEFECT_MODULE)
    next_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
    plan, level, days = DEFECT_PLANS[category]
    today = date.today()
    defect = {
        "id": next_id,
        "缺陷编号": f"DEFE-{next_id:04d}",
        "发现日期": today.isoformat(),
        "缺陷设备": entry.get("汇流箱编号"),
        "缺陷类别": category,
        "严重等级": level,
        "处理方案": plan,
        "整改时限": (today + timedelta(days=days)).isoformat(),
        "缺陷状态": "待分派",
        "status": "待分派",
        "pending": True,
        "abnormal": False,
    }
    rows.append(defect)
    return defect


class CombinerBoxService:
    def __init__(self) -> None:
        _backfill_legacy_rows()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("汇流箱编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        reasons: list[str] = []
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            reasons.append(f"缺少必填字段：{'、'.join(missing)}")
        code = str(values.get("汇流箱编号") or "").strip()
        if code:
            existing = next(
                (row for row in store.rows(MODULE) if str(row.get("汇流箱编号") or "").strip() == code),
                None,
            )
            if existing is not None:
                reasons.append(f"汇流箱 {code} 已登记（记录 #{existing.get('id')}），重复提交已打回，只保留最早一条")
        if reasons:
            return None, reasons
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in PERSISTED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["运行状态"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"汇流箱 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于汇流箱检测可执行范围"
        current = entry.get("status") if entry.get("status") in STATUS_ORDER else STATUS_ORDER[0]
        target = ACTION_RULES[action]
        if current == STATUS_ORDER[-1]:
            return None, f"汇流箱已停用，不允许再执行「{action}」"
        if target == current:
            return None, f"汇流箱已处于「{current}」，无需重复{action}"
        if STATUS_ORDER.index(current) - STATUS_ORDER.index(target) > 1:
            previous = STATUS_ORDER[STATUS_ORDER.index(current) - 1]
            return None, f"状态须沿{'→'.join(STATUS_ORDER[:3])}逐级推进，「{current}」不许直接改回「{target}」，请先按「{previous}」处置"
        entry["status"] = target
        entry["运行状态"] = target
        _sync_status_fields(entry, target)
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = target in ABNORMAL_STATUSES or action in NEGATIVE_ACTIONS
        message = f"汇流箱已{action}"
        if target in ABNORMAL_STATUSES:
            defect = _file_defect(entry, target)
            message += f"，处置结论已落入缺陷管理清单（{defect['缺陷编号']}）"
        return entry, message
