"""甲路：独立游标队列。

抓取侧的读引用唯一绑定各源站的“队列头”（capture_cursors 中 status='队列头'
的那一行，即断点落点行）。不存在第二套游标引用，结构上禁双开。

规则：
- 断点提交在单个事务内完成：抢源站锁 → 校验指纹链 → 旧头归档 → 写新头 → 释放锁；
  任一步失败整体回滚，并向调用方吐白话错误。
- 没有断点落点行时，重抽请求直接拒绝（禁止整页重抽把旧页再灌进隔离舱）。
- 重抽必须携带与队列头一致的指纹与页键，指纹偏了即拒。
- 重抽对源站维度已见过的条目键去重，重复键拦截计数，不重复灌舱。
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading

from app.db import get_db
from app.services import audit as audit_svc
from app.services.errors import ServiceError

# 唯一读轨：抓取列表只认这个引用，没有 old_track / live_flag 之类的第二轨。
READ_REF = "cursor_queue_head"
MODE = "甲路-独立游标队列"
HEAD = "队列头"
ARCHIVED = "已归档"
CHIP_OK = "断点已亮"
CHIP_BAD = "仍整页重抽"


def fingerprint_of(
    source_name: str, page_key: str, last_item_key: str, prev_fingerprint: str
) -> str:
    raw = f"{source_name}|{page_key}|{last_item_key or ''}|{prev_fingerprint or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _head_row(conn, source_name: str):
    return conn.execute(
        "SELECT * FROM capture_cursors WHERE source_name=? AND status='队列头'",
        (source_name,),
    ).fetchone()


def _acquire_lock(conn, source_name: str) -> str:
    holder = f"thread-{threading.get_ident()}"
    try:
        conn.execute(
            "INSERT INTO cursor_locks(source_name, holder) VALUES (?, ?)",
            (source_name, holder),
        )
    except sqlite3.IntegrityError:
        raise ServiceError(
            "断点写不进去：该源站的断点正被另一路提交占用，串行锁未拿到，本次未写定",
            409,
        )
    return holder


def _release_lock(conn, source_name: str) -> None:
    conn.execute("DELETE FROM cursor_locks WHERE source_name=?", (source_name,))


def _head_view(row) -> dict:
    """断点芯片只由落点行是否存在推导，杜绝芯片与落点拧花。"""
    if row is None:
        return {
            "has_landing": False,
            "chip": CHIP_BAD,
            "read_ref": "",
            "page_key": "",
            "last_item_key": "",
            "fingerprint": "",
            "fingerprint_short": "",
            "version": 0,
            "re_pull_count": 0,
            "blocked_dup_count": 0,
        }
    return {
        "has_landing": True,
        "chip": CHIP_OK,
        "read_ref": READ_REF,
        "page_key": row["page_key"],
        "last_item_key": row["last_item_key"],
        "fingerprint": row["fingerprint"],
        "fingerprint_short": row["fingerprint"][:16],
        "version": row["version"],
        "re_pull_count": row["re_pull_count"],
        "blocked_dup_count": row["blocked_dup_count"],
    }


def get_head(source_name: str, db_path: str | None = None) -> dict:
    source_name = (source_name or "").strip()
    with get_db(db_path) as conn:
        view = _head_view(_head_row(conn, source_name))
    view["source_name"] = source_name
    return view


def commit_checkpoint(
    source_name: str,
    page_key: str,
    last_item_key: str = "",
    expect_fingerprint: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """一次提交写定断点钉点；失败整体回滚并返回白话错误。"""
    source_name = (source_name or "").strip()
    page_key = (page_key or "").strip()
    last_item_key = (last_item_key or "").strip()
    if not source_name:
        raise ServiceError("断点写不进去：源站名不能为空")
    if not page_key:
        raise ServiceError("断点写不进去：断点页键不能为空")

    try:
        with get_db(db_path) as conn:
            _acquire_lock(conn, source_name)
            old = _head_row(conn, source_name)
            prev_fp = old["fingerprint"] if old else ""
            if old is not None:
                # 已有钉点：必须携带与队列头一致的指纹才允许推进，防旧游标回刷覆盖。
                if not expect_fingerprint:
                    raise ServiceError(
                        "断点写不进去：缺少当前队列头指纹，禁止裸写覆盖断点钉点",
                        409,
                    )
                if expect_fingerprint != old["fingerprint"]:
                    raise ServiceError(
                        "断点写不进去：携带的指纹与队列头不符，疑似旧游标回刷，本次未写定",
                        409,
                    )
            elif expect_fingerprint:
                raise ServiceError(
                    "断点写不进去：该源站尚无断点落点，首钉不应携带旧指纹",
                    409,
                )

            fp = fingerprint_of(source_name, page_key, last_item_key, prev_fp)
            version = (old["version"] + 1) if old else 1
            carry_pulls = old["re_pull_count"] if old else 0
            carry_blocked = old["blocked_dup_count"] if old else 0
            if old is not None:
                conn.execute(
                    "UPDATE capture_cursors SET status=? WHERE id=?",
                    (ARCHIVED, old["id"]),
                )
            try:
                cur = conn.execute(
                    """
                    INSERT INTO capture_cursors(
                      source_name, page_key, last_item_key, fingerprint, prev_fingerprint,
                      version, re_pull_count, blocked_dup_count, status, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_name,
                        page_key,
                        last_item_key,
                        fp,
                        prev_fp,
                        version,
                        carry_pulls,
                        carry_blocked,
                        HEAD,
                        note or "",
                    ),
                )
            except sqlite3.IntegrityError:
                # 队列头唯一索引兜底：同源站第二个钉点落不进来。
                raise ServiceError(
                    "断点写不进去：该源站已有队列头钉点，重复钉点被唯一约束拒绝",
                    409,
                )
            _release_lock(conn, source_name)
            cursor_id = cur.lastrowid
            row = conn.execute(
                "SELECT * FROM capture_cursors WHERE id=?", (cursor_id,)
            ).fetchone()
            view = _head_view(row)
    except ServiceError:
        raise
    except sqlite3.Error as exc:
        raise ServiceError(f"断点写不进去：数据库提交失败，本次未写定（{exc}）", 500)

    audit_svc.write_event(
        "落断点",
        "capture_cursor",
        cursor_id,
        f"{source_name} {page_key} v{version} {fp[:16]}",
        db_path=db_path,
    )
    out = {"source_name": source_name, "mode": MODE, **view}
    return out


def _seen_keys(conn, source_name: str, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT DISTINCT i.item_key
        FROM capture_items i
        JOIN capture_batches b ON b.id = i.batch_id
        WHERE b.source_name = ? AND i.item_key IN ({placeholders})
        """,
        [source_name, *keys],
    ).fetchall()
    return {r["item_key"] for r in rows}


def re_pull_page(
    source_name: str,
    page_key: str,
    fingerprint: str,
    batch_id: int,
    items: list[dict] | None = None,
    db_path: str | None = None,
) -> dict:
    """按队列头断点重抽当刻页；无落点/指纹偏/旧页重复键一律拒绝或拦截。"""
    source_name = (source_name or "").strip()
    page_key = (page_key or "").strip()
    items = items or []
    try:
        with get_db(db_path) as conn:
            _acquire_lock(conn, source_name)
            head = _head_row(conn, source_name)
            if head is None:
                raise ServiceError(
                    "重抽被拒：该源站还没有断点落点行，禁止整页重抽把旧页再灌进隔离舱",
                    409,
                )
            if not fingerprint or fingerprint != head["fingerprint"]:
                raise ServiceError(
                    "重抽被拒：携带的断点指纹与队列头不符，旧页游标已失效，禁止回刷",
                    409,
                )
            if page_key != head["page_key"]:
                raise ServiceError(
                    f"重抽被拒：页键 {page_key} 与断点落点页 {head['page_key']} 不一致",
                    409,
                )
            batch = conn.execute(
                "SELECT * FROM capture_batches WHERE id=?", (batch_id,)
            ).fetchone()
            if not batch:
                raise ServiceError("重抽被拒：目标批次不存在", 404)
            if batch["status"] == "已关闭":
                raise ServiceError("重抽被拒：目标批次已关闭，不能再灌条目")
            if batch["source_name"] != source_name:
                raise ServiceError("重抽被拒：目标批次不属于该源站")

            keys = [str(it.get("item_key", "")).strip() for it in items]
            keys = [k for k in keys if k]
            if len(keys) != len(items):
                raise ServiceError("重抽被拒：存在空条目键")
            seen = _seen_keys(conn, source_name, keys)
            pulled = 0
            for it in items:
                key = str(it.get("item_key", "")).strip()
                if key in seen:
                    # 旧页已见键或本次入参内的重复键，一律拦截不灌舱。
                    continue
                quality = it.get("quality", "合格")
                if quality not in ("合格", "缺字段", "编码空", "重复键"):
                    raise ServiceError("重抽被拒：质量标记无效")
                conn.execute(
                    """
                    INSERT INTO capture_items(batch_id, item_key, payload, quality, status)
                    VALUES (?, ?, ?, ?, '待检')
                    """,
                    (batch_id, key, str(it.get("payload", "") or ""), quality),
                )
                seen.add(key)
                pulled += 1
            blocked = len(items) - pulled
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM capture_items WHERE batch_id=?", (batch_id,)
            ).fetchone()["c"]
            bad = conn.execute(
                "SELECT COUNT(*) AS c FROM capture_items WHERE batch_id=? AND quality!='合格'",
                (batch_id,),
            ).fetchone()["c"]
            conn.execute(
                "UPDATE capture_batches SET item_count=?, bad_count=? WHERE id=?",
                (cnt, bad, batch_id),
            )
            conn.execute(
                """
                UPDATE capture_cursors
                SET re_pull_count = re_pull_count + 1,
                    blocked_dup_count = blocked_dup_count + ?
                WHERE id=?
                """,
                (blocked, head["id"]),
            )
            row = conn.execute(
                "SELECT * FROM capture_cursors WHERE id=?", (head["id"],)
            ).fetchone()
            _release_lock(conn, source_name)
            view = _head_view(row)
    except ServiceError:
        raise
    except sqlite3.Error as exc:
        raise ServiceError(f"重抽被拒：数据库提交失败，本次未写定（{exc}）", 500)

    audit_svc.write_event(
        "断点重抽",
        "capture_cursor",
        head["id"],
        f"{source_name} {page_key} 新灌{pulled} 拦截重复{blocked}",
        db_path=db_path,
    )
    return {
        "source_name": source_name,
        "batch_id": batch_id,
        "pulled": pulled,
        "blocked_dup": blocked,
        "mode": MODE,
        **view,
    }


def _all_sources(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT site_name AS name FROM source_sites
        UNION
        SELECT source_name AS name FROM capture_batches
        ORDER BY name
        """
    ).fetchall()
    return [r["name"] for r in rows]


def cursor_summary(db_path: str | None = None) -> dict:
    """对账页/设置页共用的游标摘要：芯片、重抽次数、落点行同出一源。"""
    with get_db(db_path) as conn:
        sources = _all_sources(conn)
        items = []
        total_pulls = 0
        total_blocked = 0
        for name in sources:
            view = _head_view(_head_row(conn, name))
            view["source_name"] = name
            # 芯片与重抽次数拧花校验：无落点行时重抽次数必须为 0 且芯片为坏。
            view["chip_count_aligned"] = (
                view["has_landing"]
                and view["chip"] == CHIP_OK
                and view["read_ref"] == READ_REF
            ) or (
                not view["has_landing"]
                and view["chip"] == CHIP_BAD
                and view["re_pull_count"] == 0
            )
            total_pulls += view["re_pull_count"]
            total_blocked += view["blocked_dup_count"]
            items.append(view)
        locks = [
            dict(r)
            for r in conn.execute(
                "SELECT source_name, holder, locked_at FROM cursor_locks ORDER BY source_name"
            ).fetchall()
        ]
        archived = conn.execute(
            "SELECT COUNT(*) AS c FROM capture_cursors WHERE status=?", (ARCHIVED,)
        ).fetchone()["c"]
    landed = sum(1 for i in items if i["has_landing"])
    return {
        "mode": MODE,
        "read_ref": READ_REF,
        "dual_open": False,
        "chip_ok": CHIP_OK,
        "chip_bad": CHIP_BAD,
        "sources": items,
        "totals": {
            "sources": len(items),
            "landed": landed,
            "headless": len(items) - landed,
            "re_pull_count": total_pulls,
            "blocked_dup_count": total_blocked,
            "archived_heads": archived,
        },
        "locks": locks,
        # 芯片由落点行推导，结构上不可能拧花。
        "all_aligned": all(i["chip_count_aligned"] for i in items),
    }


def head_map(db_path: str | None = None) -> dict[str, dict]:
    """抓取列表按源站名取队列头引用（唯一一套读引用）。"""
    summary = cursor_summary(db_path)
    return {item["source_name"]: item for item in summary["sources"]}
