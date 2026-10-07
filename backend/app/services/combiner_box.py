"""汇流箱检测业务规则：字段回填、状态推进、重复登记拦截与缺陷联动都收在这里。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "combiner_box"
DEFECT_MODULE = "defect"

# 登记口径：以下字段缺失或取值非法的单据一律打回，并写明原因，不许静默丢弃。
REQUIRED_FIELDS = ["汇流箱编号", "所属阵列", "输入路数", "熔断器状态", "防雷模块状态", "通讯状态"]
OPTIONAL_FIELDS = ["箱体温度", "投运日期"]
# 列表 / 详情 / 导出共用同一套列，回填口径只允许从这里出。
LIST_FIELDS = [
    "汇流箱编号",
    "所属阵列",
    "输入路数",
    "熔断器状态",
    "防雷模块状态",
    "通讯状态",
    "箱体温度",
    "投运日期",
    "运行状态",
]

STATUS_NORMAL = "运行正常"
STATUS_FUSE = "熔断器异常"
STATUS_COMM = "通讯中断"
STATUS_OFF = "已停用"
STATUS_ORDER = [STATUS_NORMAL, STATUS_FUSE, STATUS_COMM, STATUS_OFF]

# 三个采集状态的合法取值；登记时按同一口径校验。
FUSE_VALUES = ["正常", "异常"]
SPD_VALUES = ["正常", "异常"]
COMM_VALUES = ["正常", "中断"]
CHOICE_FIELDS = {
    "熔断器状态": FUSE_VALUES,
    "防雷模块状态": SPD_VALUES,
    "通讯状态": COMM_VALUES,
}

ACTION_RULES = {
    "恢复正常": STATUS_NORMAL,
    "标记异常": STATUS_FUSE,
    "标记通讯中断": STATUS_COMM,
    "停用设备": STATUS_OFF,
}

# 推进到异常状态时，同步在缺陷管理清单里落一条处置结论。
DEFECT_PROFILE = {
    STATUS_FUSE: {
        "缺陷类别": "熔断器异常",
        "严重等级": "一般",
        "处理方案": "检查故障支路熔断器，更换熔断件并排查支路过流原因",
        "整改时限": "7日内",
    },
    STATUS_COMM: {
        "缺陷类别": "通讯中断",
        "严重等级": "紧急",
        "处理方案": "检查通讯模块、通讯线缆与数据采集链路，恢复数据上报",
        "整改时限": "24小时内",
    },
}
OPEN_DEFECT_STATUS = ("待分派", "处理中")

# 存量记录回填投运日期时的起始日，按投运顺序（id 先后）逐日排开。
LEGACY_BASE_DATE = date(2026, 9, 1)


class CombinerBoxService:
    def __init__(self) -> None:
        # 服务装载即治理存量：回填投运日期、重复编号只留最早一条。
        self._backfill_legacy()

    # ------------------------------------------------------------------
    # 存量治理
    # ------------------------------------------------------------------
    def _backfill_legacy(self) -> None:
        """存量记录按投运顺序回填投运日期；箱体温度沿用原有采集口径，一律不动。

        同一汇流箱编号的重复存量只保留最早登记的一条。
        """
        rows = store.rows(MODULE)
        ordered = sorted(rows, key=lambda row: int(row.get("id", 0)))
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in ordered:
            code = str(row.get("汇流箱编号") or "").strip()
            if code and code in seen:
                continue
            seen.add(code)
            deduped.append(row)
        rows[:] = deduped

        first_id = min((int(row.get("id", 1)) for row in deduped), default=1)
        for row in deduped:
            if not str(row.get("投运日期") or "").strip():
                offset = int(row.get("id", first_id)) - first_id
                row["投运日期"] = (LEGACY_BASE_DATE + timedelta(days=offset)).isoformat()
            # 注意：箱体温度属于历史采集口径，回填时不换算、不覆盖。

    # ------------------------------------------------------------------
    # 统一回填口径
    # ------------------------------------------------------------------
    def _serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """列表、详情、导出统一出口：运行状态一律以状态机为准，三页必须对得上。"""
        item: dict[str, Any] = {"id": row.get("id")}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            item[field] = row.get(field)
        item["运行状态"] = row.get("status", STATUS_NORMAL)
        return item

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
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
        return [self._serialize(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._serialize(row) if row is not None else None

    # ------------------------------------------------------------------
    # 登记
    # ------------------------------------------------------------------
    def _derive_status(self, values: dict[str, str]) -> str:
        """新建时按通讯状态、熔断器状态的采集值推导箱体状态。"""
        if values["通讯状态"] == "中断":
            return STATUS_COMM
        if values["熔断器状态"] == "异常":
            return STATUS_FUSE
        return STATUS_NORMAL

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        normalized = {
            field: str(values.get(field) or "").strip()
            for field in REQUIRED_FIELDS + OPTIONAL_FIELDS
        }
        missing = [field for field in REQUIRED_FIELDS if not normalized[field]]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}，请补齐后重新提交"

        for field, choices in CHOICE_FIELDS.items():
            if normalized[field] not in choices:
                return None, f"{field}仅支持 {'/'.join(choices)}，收到「{normalized[field]}」，单据打回"

        code = normalized["汇流箱编号"]
        rows = store.rows(MODULE)
        earliest = next(
            (row for row in rows if str(row.get("汇流箱编号") or "").strip() == code),
            None,
        )
        if earliest is not None:
            return (
                None,
                f"汇流箱编号 {code} 已存在（记录 {earliest.get('id')}），重复提交只保留最早登记的一条",
            )

        status = self._derive_status(normalized)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1
        }
        for field in REQUIRED_FIELDS:
            entry[field] = normalized[field]
        entry["箱体温度"] = normalized["箱体温度"]
        entry["投运日期"] = normalized["投运日期"] or date.today().isoformat()
        entry["status"] = status
        entry["pending"] = status in (STATUS_FUSE, STATUS_COMM)
        entry["abnormal"] = status in (STATUS_FUSE, STATUS_COMM)
        rows.append(entry)

        if status in DEFECT_PROFILE:
            self._raise_defect(entry, status)
        return self._serialize(entry), ""

    # ------------------------------------------------------------------
    # 状态推进
    # ------------------------------------------------------------------
    def _apply_status(
        self, entry: dict[str, Any], status: str, action: str
    ) -> tuple[dict[str, Any], str]:
        entry["status"] = status
        entry["pending"] = status in (STATUS_FUSE, STATUS_COMM)
        entry["abnormal"] = status in (STATUS_FUSE, STATUS_COMM)
        return self._serialize(entry), f"汇流箱已{action}"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"汇流箱 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于汇流箱检测可执行范围"

        code = str(entry.get("汇流箱编号") or "")
        current = str(entry.get("status") or STATUS_NORMAL)
        target = ACTION_RULES[action]

        if current == STATUS_OFF:
            return None, "汇流箱已停用，停用状态为终态，不能再变更运行状态"

        if action == "停用设备":
            return self._apply_status(entry, STATUS_OFF, action)

        if action == "恢复正常":
            if current == STATUS_COMM:
                # 硬性规则：通讯中断不许直接改回运行正常，须先在缺陷管理里闭环通讯故障。
                return None, "通讯中断的箱体须先在缺陷管理闭环通讯故障，不许直接改回运行正常"
            if current == STATUS_NORMAL:
                return None, "当前已是运行正常，无需恢复"
            # 仅允许从熔断器异常恢复。
            self._close_defects(code, DEFECT_PROFILE[STATUS_FUSE]["缺陷类别"])
            entry["熔断器状态"] = "正常"
            return self._apply_status(entry, STATUS_NORMAL, action)

        if action == "标记异常":
            if current == STATUS_FUSE:
                return None, "当前已是熔断器异常，请勿重复标记"
            if current == STATUS_COMM:
                return None, "已推进至通讯中断，不能回退为熔断器异常"
            entry["熔断器状态"] = "异常"
            applied, message = self._apply_status(entry, STATUS_FUSE, action)
            self._raise_defect(entry, STATUS_FUSE)
            return applied, message

        if action == "标记通讯中断":
            if current == STATUS_COMM:
                return None, "当前已是通讯中断，请勿重复标记"
            if current == STATUS_NORMAL:
                return None, "状态只能沿 运行正常 → 熔断器异常 → 通讯中断 推进，不允许跨级标记"
            entry["通讯状态"] = "中断"
            applied, message = self._apply_status(entry, STATUS_COMM, action)
            self._raise_defect(entry, STATUS_COMM)
            return applied, message

        return None, f"动作「{action}」当前状态「{current}」下不允许执行"

    # ------------------------------------------------------------------
    # 缺陷联动
    # ------------------------------------------------------------------
    def _raise_defect(self, entry: dict[str, Any], status: str) -> None:
        """把异常箱体落到缺陷管理清单；同一箱体同一类未闭环缺陷不重复登记。"""
        profile = DEFECT_PROFILE[status]
        code = str(entry.get("汇流箱编号") or "")
        defects = store.rows(DEFECT_MODULE)
        already_open = any(
            row.get("缺陷设备") == code
            and row.get("缺陷类别") == profile["缺陷类别"]
            and row.get("status") in OPEN_DEFECT_STATUS
            for row in defects
        )
        if already_open:
            return
        next_id = max((int(row.get("id", 0)) for row in defects), default=0) + 1
        defects.append({
            "id": next_id,
            "status": "待分派",
            "pending": True,
            "abnormal": True,
            "缺陷编号": f"DEFE-{next_id:04d}",
            "发现日期": date.today().isoformat(),
            "缺陷设备": code,
            "缺陷类别": profile["缺陷类别"],
            "严重等级": profile["严重等级"],
            "处理方案": profile["处理方案"],
            "整改时限": profile["整改时限"],
            "缺陷状态": "待分派",
            "来源模块": MODULE,
            "来源记录": entry.get("id"),
        })

    def _close_defects(self, code: str, category: str) -> None:
        """箱体恢复时把处置结论回写到缺陷清单，未闭环缺陷验收销项。"""
        for row in store.rows(DEFECT_MODULE):
            if (
                row.get("缺陷设备") == code
                and row.get("缺陷类别") == category
                and row.get("status") in OPEN_DEFECT_STATUS
            ):
                row["status"] = "已验收"
                row["缺陷状态"] = "已验收"
                row["pending"] = False
                row["abnormal"] = False
                plan = str(row.get("处理方案") or "").strip()
                conclusion = "汇流箱复检运行正常，处置结论：验收销项"
                row["处理方案"] = f"{plan}；{conclusion}" if plan else conclusion
