"""Deduplicate Bili23's queue and make the deletion survive a WAL checkpoint.

Why the previous attempt looked like it worked and did not: the DELETE was
committed into ``task.db-wal``; removing that file afterwards threw the
transaction away, so the app woke up to the old queue again.  Correct order is
delete -> commit -> ``wal_checkpoint(TRUNCATE)`` -> close -> **reopen and count**
before anything else touches the file.
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

DB = pathlib.Path.home() / "AppData" / "Roaming" / "Bili23 Downloader" / "task.db"


def counts(con: sqlite3.Connection) -> dict:
    cur = con.cursor()
    out = {}
    for table in ("download_task", "completed_task"):
        try:
            out[table] = cur.execute(f"select count(*) from {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = -1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge-queue", action="store_true",
                    help="delete every queued row (keeps completed history)")
    ap.add_argument("--dedupe", action="store_true",
                    help="keep the first row per title, delete the rest")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)

    con = sqlite3.connect(DB)
    cur = con.cursor()
    print("journal_mode:", cur.execute("pragma journal_mode").fetchone()[0])
    print("before:", counts(con))

    if args.show:
        rows = cur.execute("select id, title from download_task order by id").fetchall()
        seen, dupes = set(), []
        for _id, title in rows:
            if title in seen:
                dupes.append(title)
            seen.add(title)
        print("titles:", [t for _i, t in rows])
        print(f"duplicate titles: {len(dupes)} -> {sorted(set(dupes))}")
        return 0

    if args.purge_queue:
        cur.execute("delete from download_task")
    elif args.dedupe:
        cur.execute("""
            delete from download_task
             where id not in (select min(id) from download_task group by title)
        """)
    else:
        print("nothing to do; pass --purge-queue or --dedupe")
        return 0

    con.commit()
    # Without this the commit lives only in the -wal file.
    cur.execute("pragma wal_checkpoint(TRUNCATE)")
    con.commit()
    print("after commit+checkpoint:", counts(con))
    con.close()

    # Re-open from scratch: proof the change actually hit the main database.
    verify = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    result = counts(verify)
    verify.close()
    print("verified on reopen:", result)
    if result.get("download_task") != 0:
        print("WARNING: queue is not empty after checkpoint", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
