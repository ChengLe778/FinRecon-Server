# -*- coding: utf-8 -*-
"""对账引擎（纯函数）：不碰数据库，只做匹配计算。

输入：未核销发票列表 + 未核销流水列表
输出：
  auto       自动核销对 —— 同一往来单位 + 未核销金额【精确相等】+ 日期合理
  diffs      差异池     —— 单位一致但金额不等，绝不自动核销，供人工处理
  candidates 候选推荐   —— 跨单位/弱相关，仅供提示

核心规则（本次升级重点）：
    公司名（往来单位）一致、但金额不一致 → 一律【不自动核销】。
    这类组合进入「差异池」，并给出四种处理建议：
      partial  流水 < 发票 → 部分核销，余额继续挂账
      over     流水 > 发票 → 核销发票全额，流水剩余转为预收/预付
      near     差额极小   → 差额核销（差额记为抹零/手续费，单独统计）
      combo    一票多笔 / 一笔多票 → 凑单核销
    报表端始终以「发票未核销额」为准，未匹配流水计入预收/预付，
    因此不核销也不会让数据消失，只是挂着等待人工判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from ..config import (
    CANDIDATE_TOLERANCE, CANDIDATE_MAX_COMBO,
    CANDIDATE_MAX_DAYS_BEFORE_INVOICE, CANDIDATE_LIMIT,
    AUTO_AMOUNT_TOLERANCE, AUTO_DAYS_BEFORE, AUTO_DAYS_AFTER,
    DIFF_SMALL_AMOUNT, DIFF_SMALL_RATIO, DIFF_LIMIT,
)

ZERO = Decimal("0.00")

# 差异池建议动作
ACTION_PARTIAL = "partial"    # 部分核销
ACTION_OVER = "over"          # 超额核销（流水剩余转预收/预付）
ACTION_NEAR = "near"          # 差额核销
ACTION_COMBO = "combo"        # 凑单核销
ACTION_IGNORE = "ignore"      # 标记无关


@dataclass
class InvSide:
    """参与匹配的发票侧"""
    id: int
    invoice_no: Optional[str]
    invoice_date: Optional[str]
    total: Decimal
    remaining: Decimal
    cp_key: str
    cp_name: Optional[str]
    direction: str = ""
    category: Optional[str] = None


@dataclass
class TxnSide:
    """参与匹配的流水侧"""
    id: int
    txn_date: Optional[str]
    amount: Decimal
    remaining: Decimal
    cp_key: str
    cp_name: Optional[str]
    bank: str = ""
    memo: Optional[str] = None
    direction: str = ""


@dataclass
class Pair:
    """一条匹配结果"""
    kind: str                      # auto | partial | over | near | combo | approx
    invoice_ids: List[int]
    txn_ids: List[int]
    amount: Decimal
    score: float = 0.0
    reason: str = ""
    diff_amount: Decimal = ZERO   # 差异金额（发票额 - 流水额 的绝对值）
    action: str = ACTION_IGNORE   # 建议动作
    action_label: str = ""
    detail: dict = field(default_factory=dict)


def _ratio_diff(a: Decimal, b: Decimal) -> float:
    """a 相对 b 的偏差比例（0 表示完全相等）。"""
    if b == 0 or a == 0:
        return 1.0
    return float(abs(a - b) / (b if b > 0 else -b))


def _days_between(d1: Optional[str], d2: Optional[str]) -> Optional[int]:
    """d2 - d1 的天数。"""
    from datetime import date
    def _p(v):
        if not v:
            return None
        if isinstance(v, date):
            return v
        try:
            y, m, d = str(v)[:10].split("-")
            return date(int(y), int(m), int(d))
        except Exception:
            return None
    a, b = _p(d1), _p(d2)
    if not a or not b:
        return None
    return (b - a).days


def _date_ok(inv_date: Optional[str], txn_date: Optional[str]) -> bool:
    """自动核销的日期窗口校验：流水不能太早也不能太晚。"""
    gap = _days_between(inv_date, txn_date)
    if gap is None:
        return True                       # 缺日期时不因日期否决
    return -AUTO_DAYS_BEFORE <= gap <= AUTO_DAYS_AFTER


def _is_near(diff: Decimal, base: Decimal) -> bool:
    """是否属于「小额差异」（抹零 / 手续费 / 汇损）。"""
    if diff <= 0:
        return False
    if diff > DIFF_SMALL_AMOUNT:
        return False
    if base > 0 and diff / base > DIFF_SMALL_RATIO:
        return False
    return True


def _suggest(inv_rem: Decimal, txn_rem: Decimal) -> Tuple[str, str, str]:
    """给出差异处理建议：(kind, action, 说明)"""
    diff = abs(inv_rem - txn_rem)
    if diff == 0:
        return "auto", ACTION_NEAR, "金额精确相等"
    if _is_near(diff, min(inv_rem, txn_rem)):
        return ("near", ACTION_NEAR,
                f"差额仅 {diff} 元，建议差额核销（记为抹零/手续费）")
    if txn_rem < inv_rem:
        return ("partial", ACTION_PARTIAL,
                f"流水比发票少 {diff} 元，建议部分核销，余额 {inv_rem - txn_rem} 元继续挂账")
    return ("over", ACTION_OVER,
            f"流水比发票多 {diff} 元，建议核销发票全额，剩余 {txn_rem - inv_rem} 元转为预收/预付")


def match(invoices: List[InvSide], txns: List[TxnSide]) -> dict:
    """执行匹配。返回 {'auto': [...], 'diffs': [...], 'candidates': [...], 'stats': {...}}"""
    auto: List[Pair] = []
    diffs: List[Pair] = []
    candidates: List[Pair] = []

    used_inv: set = set()
    used_txn: set = set()

    # 按往来单位分组，只在组内匹配
    inv_by_cp: Dict[str, List[InvSide]] = {}
    for i in invoices:
        if i.remaining <= 0:
            continue
        inv_by_cp.setdefault(i.cp_key, []).append(i)
    txn_by_cp: Dict[str, List[TxnSide]] = {}
    for t in txns:
        if t.remaining <= 0:
            continue
        txn_by_cp.setdefault(t.cp_key, []).append(t)

    # ---------------- 规则 1：精确相等 → 自动核销 ----------------
    # 严格：金额必须分文不差（AUTO_AMOUNT_TOLERANCE 默认 0），且日期落在合理窗口内。
    # 金额不等的一律不进这里 —— 这是本次升级的关键约束。
    for cp_key, invs in inv_by_cp.items():
        pool = sorted(txn_by_cp.get(cp_key, []),
                      key=lambda x: (str(x.txn_date or ""), x.id))
        for inv in sorted(invs, key=lambda x: (str(x.invoice_date or ""), x.id)):
            hit = None
            hit_gap = None
            for t in pool:
                if t.id in used_txn or t.remaining <= 0:
                    continue
                if abs(t.remaining - inv.remaining) > AUTO_AMOUNT_TOLERANCE:
                    continue                      # 金额不等：不允许自动核销
                if not _date_ok(inv.invoice_date, t.txn_date):
                    continue                      # 日期明显不合理
                gap = _days_between(inv.invoice_date, t.txn_date)
                # 优先取付款日不早于开票日、且间隔最短的那一笔
                score_gap = gap if gap is not None else 10 ** 6
                if hit is None:
                    hit, hit_gap = t, score_gap
                else:
                    cur_bad = (hit_gap is None or hit_gap < 0 or hit_gap == 10 ** 6)
                    new_good = (score_gap is not None and 0 <= score_gap < 10 ** 6)
                    if cur_bad and new_good:
                        hit, hit_gap = t, score_gap
                    elif (not cur_bad) and new_good and score_gap < (hit_gap or 10 ** 6):
                        hit, hit_gap = t, score_gap
            if hit is not None:
                auto.append(Pair(
                    kind="auto",
                    invoice_ids=[inv.id],
                    txn_ids=[hit.id],
                    amount=min(inv.remaining, hit.remaining),
                    score=1.0,
                    reason="同一往来单位、金额精确相等",
                    diff_amount=ZERO,
                    action=ACTION_NEAR,
                    action_label="自动核销",
                    detail={"cp_name": inv.cp_name or hit.cp_name,
                            "invoice_no": inv.invoice_no,
                            "invoice_date": str(inv.invoice_date or ""),
                            "txn_date": str(hit.txn_date or ""),
                            "bank": hit.bank,
                            "memo": hit.memo},
                ))
                used_inv.add(inv.id)
                used_txn.add(hit.id)

    # ---------------- 规则 2：差异池（同单位、金额不等）----------------
    for cp_key, invs in inv_by_cp.items():
        pool = [t for t in txn_by_cp.get(cp_key, [])
                if t.id not in used_txn and t.remaining > 0]
        open_invs = [i for i in invs if i.id not in used_inv and i.remaining > 0]
        if not pool or not open_invs:
            continue

        # 2a. 单票对单笔：金额不等 → 给建议，绝不自动核销
        for inv in open_invs:
            for t in pool:
                gap = _days_between(inv.invoice_date, t.txn_date)
                if gap is not None and gap < -CANDIDATE_MAX_DAYS_BEFORE_INVOICE:
                    continue  # 流水比发票早太多，明显不相关

                inv_rem, txn_rem = inv.remaining, t.remaining
                if abs(inv_rem - txn_rem) <= AUTO_AMOUNT_TOLERANCE:
                    continue  # 金额相等但日期越界的，交给下面的候选逻辑

                kind, action, reason = _suggest(inv_rem, txn_rem)
                diff = abs(inv_rem - txn_rem)

                # 打分：差额越小越优先；日期越接近越优先
                ratio = _ratio_diff(txn_rem, inv_rem)
                score = 90.0 - min(ratio, 1.0) * 40
                if gap is not None:
                    if gap >= 0:
                        score += max(0.0, 12.0 - min(gap, 365) / 30.0)
                    else:
                        score -= min(15.0, abs(gap) / 30.0)
                if kind == "near":
                    score += 25.0            # 小额差异最可能是同一笔业务

                diffs.append(Pair(
                    kind=kind,
                    invoice_ids=[inv.id],
                    txn_ids=[t.id],
                    amount=min(inv_rem, txn_rem),
                    score=round(score, 2),
                    reason=reason,
                    diff_amount=diff,
                    action=action,
                    action_label={"partial": "部分核销", "over": "超额核销",
                                  "near": "差额核销"}.get(action, "人工判断"),
                    detail={"cp_name": inv.cp_name or t.cp_name,
                            "invoice_no": inv.invoice_no,
                            "invoice_date": str(inv.invoice_date or ""),
                            "invoice_remaining": str(inv_rem),
                            "txn_date": str(t.txn_date or ""),
                            "txn_remaining": str(txn_rem),
                            "bank": t.bank,
                            "memo": t.memo},
                ))

        # 2b. 一笔流水冲多张发票（发票合计 = 流水金额）
        for t in pool:
            invs_sorted = sorted(open_invs, key=lambda x: x.remaining, reverse=True)
            n = min(CANDIDATE_MAX_COMBO, len(invs_sorted), 12)
            found = False
            for size in range(2, n + 1):
                for combo in combinations(invs_sorted[:12], size):
                    s = sum((x.remaining for x in combo), ZERO)
                    if abs(s - t.remaining) > AUTO_AMOUNT_TOLERANCE:
                        if _ratio_diff(s, t.remaining) > float(CANDIDATE_TOLERANCE):
                            continue
                        score = 78.0 - _ratio_diff(s, t.remaining) * 100
                        reason = "多张发票合计与流水金额近似"
                    else:
                        score, reason = 95.0, "多张发票合计与流水金额精确相等"
                    found = True
                    diffs.append(Pair(
                        kind=ACTION_COMBO,
                        invoice_ids=[x.id for x in combo],
                        txn_ids=[t.id],
                        amount=min(s, t.remaining),
                        score=round(score, 2),
                        reason=reason,
                        diff_amount=abs(s - t.remaining),
                        action=ACTION_COMBO,
                        action_label="凑单核销（一笔冲多票）",
                        detail={"cp_name": (combo[0].cp_name or t.cp_name),
                                "invoice_nos": [x.invoice_no for x in combo],
                                "invoice_total": str(s),
                                "txn_date": str(t.txn_date or ""),
                                "txn_remaining": str(t.remaining),
                                "bank": t.bank,
                                "memo": t.memo},
                    ))
                if found:
                    break  # 已找到该流水的最佳组合，不再扩大规模

        # 2c. 多笔流水冲一张发票（反向凑单）
        for inv in open_invs:
            if len(pool) < 2 or len(pool) > 20:
                continue
            found = False
            for size in range(2, min(CANDIDATE_MAX_COMBO, len(pool)) + 1):
                for combo in combinations(pool[:15], size):
                    s = sum((x.remaining for x in combo), ZERO)
                    if abs(s - inv.remaining) <= AUTO_AMOUNT_TOLERANCE:
                        score, reason = 93.0, "多笔流水合计与发票金额精确相等"
                    elif _ratio_diff(s, inv.remaining) <= float(CANDIDATE_TOLERANCE):
                        score = 76.0 - _ratio_diff(s, inv.remaining) * 100
                        reason = "多笔流水合计与发票金额近似"
                    else:
                        continue
                    found = True
                    diffs.append(Pair(
                        kind=ACTION_COMBO,
                        invoice_ids=[inv.id],
                        txn_ids=[x.id for x in combo],
                        amount=min(inv.remaining, s),
                        score=round(score, 2),
                        reason=reason,
                        diff_amount=abs(s - inv.remaining),
                        action=ACTION_COMBO,
                        action_label="凑单核销（多笔冲一票）",
                        detail={"cp_name": inv.cp_name,
                                "invoice_no": inv.invoice_no,
                                "invoice_remaining": str(inv.remaining),
                                "txn_dates": [str(x.txn_date or "") for x in combo],
                                "txn_total": str(s),
                                "bank": combo[0].bank,
                                "memos": [x.memo for x in combo]},
                    ))
                if found:
                    break

    # 差异排序：差额小的、评分高的排前面
    diffs.sort(key=lambda p: (p.diff_amount, -p.score))
    diffs = diffs[:DIFF_LIMIT]

    # ---------------- 规则 3：候选（跨单位/弱相关，仅提示）----------------
    cp_keys = set(inv_by_cp) & set(txn_by_cp)
    for cp_key in cp_keys:
        pool = [t for t in txn_by_cp.get(cp_key, [])
                if t.id not in used_txn and t.remaining > 0]
        open_invs = [i for i in inv_by_cp.get(cp_key, [])
                     if i.id not in used_inv and i.remaining > 0]
        for inv in open_invs:
            for t in pool:
                if abs(inv.remaining - t.remaining) <= AUTO_AMOUNT_TOLERANCE:
                    continue          # 金额相等的已在差异池给定方案
                diff_ratio = _ratio_diff(t.remaining, inv.remaining)
                if diff_ratio > float(CANDIDATE_TOLERANCE):
                    continue
                gap = _days_between(inv.invoice_date, t.txn_date)
                score = 60.0 - diff_ratio * 100
                if gap is not None and gap >= 0:
                    score += max(0.0, 10.0 - min(gap, 365) / 36.0)
                candidates.append(Pair(
                    kind="approx",
                    invoice_ids=[inv.id],
                    txn_ids=[t.id],
                    amount=min(inv.remaining, t.remaining),
                    score=round(score, 2),
                    reason=f"金额偏差 {diff_ratio * 100:.1f}%（阈值 "
                           f"{float(CANDIDATE_TOLERANCE) * 100:.0f}%）",
                    diff_amount=abs(inv.remaining - t.remaining),
                    action=ACTION_IGNORE,
                    action_label="人工确认",
                    detail={"cp_name": inv.cp_name or t.cp_name,
                            "invoice_no": inv.invoice_no,
                            "invoice_date": str(inv.invoice_date or ""),
                            "invoice_remaining": str(inv.remaining),
                            "txn_date": str(t.txn_date or ""),
                            "txn_remaining": str(t.remaining),
                            "bank": t.bank, "memo": t.memo},
                ))

    candidates.sort(key=lambda p: (-p.score, str(p.detail.get("txn_date", ""))))
    candidates = candidates[:CANDIDATE_LIMIT]

    diff_total = sum((p.diff_amount for p in diffs), ZERO)
    return {
        "auto": auto,
        "diffs": diffs,
        "candidates": candidates,
        "stats": {
            "invoice_count": len(invoices),
            "txn_count": len(txns),
            "auto_count": len(auto),
            "auto_amount": str(sum((p.amount for p in auto), ZERO)),
            "diff_count": len(diffs),
            "diff_amount": str(diff_total),
            "candidate_count": len(candidates),
            "tolerance": str(AUTO_AMOUNT_TOLERANCE),
        },
    }
