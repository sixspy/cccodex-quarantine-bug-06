"""游标断点甲路验收：逐条对应题面败例红线。"""
from __future__ import annotations

import threading

import pytest

from app.services import cursors, captures, quarantine
from app.services.errors import ServiceError


def _make_batch(source_name: str, db_path) -> int:
    return captures.create_batch(source_name, "断点测试批", db_path)["id"]


# 1. 无断点落点仍整页重抽 → 必须拒
def test_repull_without_landing_is_rejected(db_path):
    bid = _make_batch("站内目录乙", db_path)
    with pytest.raises(ServiceError) as exc:
        cursors.re_pull_page(
            "站内目录乙", "page-09", "whatever-fp", bid,
            [{"item_key": "k-1"}], db_path,
        )
    assert "没有断点落点行" in exc.value.message
    head = cursors.get_head("站内目录乙", db_path)
    assert head["has_landing"] is False
    assert head["chip"] == cursors.CHIP_BAD
    assert head["re_pull_count"] == 0


# 2. 仅有芯片无落点行 → 芯片只能是坏的，不可能单独亮“断点已亮”
def test_chip_derived_from_landing_row_only(db_path):
    head = cursors.get_head("站内目录乙", db_path)
    assert head["chip"] == cursors.CHIP_BAD
    assert head["read_ref"] == ""
    # 种子里甲源有落点行，芯片才可亮
    seeded = cursors.get_head("站内目录甲", db_path)
    assert seeded["has_landing"] is True
    assert seeded["chip"] == cursors.CHIP_OK
    assert seeded["read_ref"] == cursors.READ_REF
    summary = cursors.cursor_summary(db_path)
    assert summary["all_aligned"] is True


# 3. 芯片与重抽次数拧花 → 入库即拒（次数只随成功重抽涨，芯片由落点推导）
def test_chip_and_count_cannot_drift(db_path):
    bid = _make_batch("站内目录甲", db_path)
    head = cursors.get_head("站内目录甲", db_path)
    # 用正确指纹成功重抽一次，次数 +1，芯片仍为亮
    out = cursors.re_pull_page(
        "站内目录甲", head["page_key"], head["fingerprint"], bid,
        [{"item_key": "new-sku-1", "quality": "合格"}], db_path,
    )
    assert out["re_pull_count"] == 1
    assert out["chip"] == cursors.CHIP_OK
    # 坏指纹重抽被拒，次数不涨，芯片不变
    with pytest.raises(ServiceError):
        cursors.re_pull_page(
            "站内目录甲", head["page_key"], "skew-fp", bid,
            [{"item_key": "new-sku-2"}], db_path,
        )
    again = cursors.get_head("站内目录甲", db_path)
    assert again["re_pull_count"] == 1
    assert again["chip"] == cursors.CHIP_OK
    assert cursors.cursor_summary(db_path)["all_aligned"] is True


# 4. 指纹偏了还放行 → 必须拒；推进断点也必须校验指纹链
def test_skewed_fingerprint_rejected(db_path):
    cursors.commit_checkpoint("站内目录乙", "page-01", "k-1", db_path=db_path)
    head = cursors.get_head("站内目录乙", db_path)
    # 推进断点不带指纹 → 拒
    with pytest.raises(ServiceError) as exc:
        cursors.commit_checkpoint("站内目录乙", "page-02", "k-2", db_path=db_path)
    assert "缺少当前队列头指纹" in exc.value.message
    # 带错指纹 → 拒（旧游标回刷）
    with pytest.raises(ServiceError) as exc:
        cursors.commit_checkpoint(
            "站内目录乙", "page-02", "k-2", expect_fingerprint="stale-fp", db_path=db_path
        )
    assert "指纹与队列头不符" in exc.value.message
    # 正确指纹才推进
    advanced = cursors.commit_checkpoint(
        "站内目录乙", "page-02", "k-2",
        expect_fingerprint=head["fingerprint"], db_path=db_path,
    )
    assert advanced["version"] == 2
    assert advanced["page_key"] == "page-02"
    # 旧指纹现在失效
    with pytest.raises(ServiceError):
        cursors.commit_checkpoint(
            "站内目录乙", "page-03", "k-3",
            expect_fingerprint=head["fingerprint"], db_path=db_path,
        )


# 5. 禁双开：系统里只有一套读引用，乙路不存在
def test_single_read_ref_no_dual_track(db_path):
    summary = cursors.cursor_summary(db_path)
    assert summary["read_ref"] == cursors.READ_REF
    assert summary["mode"] == "甲路-独立游标队列"
    assert summary["dual_open"] is False
    for item in summary["sources"]:
        assert item["read_ref"] in ("", cursors.READ_REF)
    # 旧脚手架模块必须已清
    import importlib
    for gone in (
        "app.services.cursor_break",
        "app.services.cursor_break_cutover",
        "app.services.cursor_break_dualwrite",
        "app.services.cursor_break_audit",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(gone)


# 6. 两边同抢皆成 → 不允许：并发提交同源站，只允许一个钉点写定
def test_concurrent_commit_same_source_only_one_wins(db_path):
    errors: list[Exception] = []
    winners: list[dict] = []

    def worker(page: str) -> None:
        try:
            winners.append(
                cursors.commit_checkpoint(
                    "站内目录丙", page, f"last-{page}", db_path=db_path
                )
            )
        except ServiceError as exc:  # 串行锁/唯一约束 → 白话拒绝
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("p-a",))
    t2 = threading.Thread(target=worker, args=("p-b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(winners) == 1
    assert len(errors) == 1
    assert "断点写不进去" in errors[0].message
    # 钉点唯一，没有被双写覆盖
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM capture_cursors "
            "WHERE source_name='站内目录丙' AND status='队列头'"
        ).fetchone()[0]
        assert n == 1
        locks = conn.execute("SELECT COUNT(*) FROM cursor_locks").fetchone()[0]
        assert locks == 0  # 事务结束锁必释放
    finally:
        conn.close()


# 7. 一次提交写定、失败吐白话（含页键为空等输入失败）
def test_commit_is_atomic_with_plain_message(db_path):
    with pytest.raises(ServiceError) as exc:
        cursors.commit_checkpoint("站内目录乙", "  ", db_path=db_path)
    assert "断点写不进去" in exc.value.message
    # 失败不留锁、不落半截行
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM cursor_locks").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM capture_cursors WHERE source_name='站内目录乙'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


# 8. 主链绿后：先核重抽次数，再核拒绝重复灌舱
def test_repull_dedup_blocks_old_page_reflood(db_path):
    bid = _make_batch("站内目录甲", db_path)
    # 先灌一条，制造“旧页已见过的键”
    captures.add_item(bid, "seen-sku", "{}", "合格", db_path)
    head = cursors.get_head("站内目录甲", db_path)
    out = cursors.re_pull_page(
        "站内目录甲", head["page_key"], head["fingerprint"], bid,
        [
            {"item_key": "seen-sku", "quality": "合格"},      # 旧页重复
            {"item_key": "fresh-sku", "quality": "缺字段"},   # 新条目
        ],
        db_path,
    )
    # 先核重抽次数：成功单 +1
    assert out["re_pull_count"] == 1
    # 再核重复灌舱被拦
    assert out["pulled"] == 1
    assert out["blocked_dup"] == 1
    detail = captures.get_batch(bid, db_path)
    keys = [i["item_key"] for i in detail["items"]]
    assert keys.count("seen-sku") == 1
    assert "fresh-sku" in keys
    # 重抽不自动入舱，隔离舱行数不受影响
    assert all(r["item_key"] != "seen-sku" for r in quarantine.list_rows(db_path=db_path))


# 9. 重抽入参自身带重复键 → 拦截且不抛错，不重复灌舱
def test_repull_in_payload_duplicates_blocked(db_path):
    bid = _make_batch("站内目录甲", db_path)
    head = cursors.get_head("站内目录甲", db_path)
    out = cursors.re_pull_page(
        "站内目录甲", head["page_key"], head["fingerprint"], bid,
        [
            {"item_key": "dup-a", "quality": "合格"},
            {"item_key": "dup-a", "quality": "合格"},
            {"item_key": "dup-b", "quality": "合格"},
        ],
        db_path,
    )
    assert out["pulled"] == 2
    assert out["blocked_dup"] == 1
    detail = captures.get_batch(bid, db_path)
    keys = [i["item_key"] for i in detail["items"]]
    assert keys.count("dup-a") == 1


# 10. 旧页游标回刷（页键不符）同样拒绝
def test_repull_wrong_page_key_rejected(db_path):
    bid = _make_batch("站内目录甲", db_path)
    head = cursors.get_head("站内目录甲", db_path)
    with pytest.raises(ServiceError) as exc:
        cursors.re_pull_page(
            "站内目录甲", "page-99", head["fingerprint"], bid,
            [{"item_key": "x"}], db_path,
        )
    assert "页键" in exc.value.message


def test_archive_chain_keeps_history(db_path):
    cursors.commit_checkpoint("站内目录乙", "p1", "k1", db_path=db_path)
    h1 = cursors.get_head("站内目录乙", db_path)
    cursors.commit_checkpoint(
        "站内目录乙", "p2", "k2", expect_fingerprint=h1["fingerprint"], db_path=db_path
    )
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT status, version FROM capture_cursors WHERE source_name='站内目录乙' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("已归档", 1), ("队列头", 2)]
    totals = cursors.cursor_summary(db_path)["totals"]
    assert totals["archived_heads"] >= 1
