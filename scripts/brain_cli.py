#!/usr/bin/env python3
"""CLI for SecondBrain. Each subcommand maps to one /brain-* command.

Usage:
  python brain_cli.py add "Title" "Content" --collection Work --tags ai,rag --source URL
  python brain_cli.py search "query" --collection Work --limit 10
  python brain_cli.py show <id-or-title>
  python brain_cli.py update <id> --content "..." --tags a,b
  python brain_cli.py delete <id> [--hard]
  python brain_cli.py restore <id>
  python brain_cli.py list [--collection C] [--tag T] [--limit N] [--sort updated|created|title]
  python brain_cli.py collections
  python brain_cli.py tags [--sort usage|alpha] [--limit N]
  python brain_cli.py relate <from> <to> --type expands --strength 0.8
  python brain_cli.py related <id> [--source manual|wikilink|all]
  python brain_cli.py traverse <id> [--depth 2] [--limit 20]
  python brain_cli.py export [--collection C] [--format json|markdown|csv] [--output PATH]
  python brain_cli.py import <path> [--merge|--replace]
  python brain_cli.py stats [--collection C]
  python brain_cli.py summary [--cold-days 180]
  python brain_cli.py distill --output <path> [--tag T] [--collection C] [--query Q] [--activate]
  python brain_cli.py archive --output <path> [--older-than-days 180] [--dry-run]
  python brain_cli.py merge-brain --from <path>

Output is human-readable. Pass --json to any read command for machine output.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from brain import SecondBrain

# Windows consoles default to cp1252; emojis break. Reconfigure if possible.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def _short(s, n=120):
    s = " ".join((s or "").split())
    return s[:n] + ("..." if len(s) > n else "")


def _fmt_drawer_line(i, d):
    tags = f" [{', '.join(d['tags'])}]" if d["tags"] else ""
    coll = f" [{d['collection']}]" if d["collection"] else ""
    return (f"{i}. {d['title']}{coll}{tags}\n"
            f"   \"{_short(d['content'])}\"\n   {d['id'][:8]}")


def main():
    p = argparse.ArgumentParser(prog="brain")
    p.add_argument("--db", default=None)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("content", nargs="?")
    a.add_argument("--content-file", help="read content from this file (avoids shell escaping for long content)")
    a.add_argument("--collection"); a.add_argument("--tags"); a.add_argument("--source", action="append")

    s = sub.add_parser("search"); s.add_argument("query")
    s.add_argument("--collection"); s.add_argument("--tag"); s.add_argument("--limit", type=int, default=10)

    sh = sub.add_parser("show"); sh.add_argument("ident")

    u = sub.add_parser("update"); u.add_argument("id")
    u.add_argument("--title"); u.add_argument("--content"); u.add_argument("--tags"); u.add_argument("--collection")

    d = sub.add_parser("delete"); d.add_argument("id"); d.add_argument("--hard", action="store_true")
    r = sub.add_parser("restore"); r.add_argument("id")

    l = sub.add_parser("list")
    l.add_argument("--collection"); l.add_argument("--tag"); l.add_argument("--limit", type=int, default=20)
    l.add_argument("--offset", type=int, default=0); l.add_argument("--sort", default="updated")

    sub.add_parser("collections")
    t = sub.add_parser("tags"); t.add_argument("--sort", default="usage"); t.add_argument("--limit", type=int)

    rel = sub.add_parser("relate"); rel.add_argument("from_id"); rel.add_argument("to_id")
    rel.add_argument("--type", default="related"); rel.add_argument("--strength", type=float, default=0.5)

    rd = sub.add_parser("related"); rd.add_argument("id")
    rd.add_argument("--source", default="all"); rd.add_argument("--limit", type=int, default=20)

    tr = sub.add_parser("traverse"); tr.add_argument("id")
    tr.add_argument("--depth", type=int, default=2); tr.add_argument("--limit", type=int, default=20)

    e = sub.add_parser("export")
    e.add_argument("--collection"); e.add_argument("--format", default="json"); e.add_argument("--output")

    im = sub.add_parser("import"); im.add_argument("path")
    im.add_argument("--merge", action="store_true"); im.add_argument("--replace", action="store_true")

    st = sub.add_parser("stats"); st.add_argument("--collection")

    sm = sub.add_parser("summary")
    sm.add_argument("--cold-days", type=int, default=180,
                    help="threshold (days) for a drawer to count as 'cold'")

    di = sub.add_parser("distill")
    di.add_argument("--output", required=True, help="path for the new distilled brain.db")
    di.add_argument("--tag", action="append", help="drawer must have this tag (repeatable)")
    di.add_argument("--collection", help="drawer must be in this collection")
    di.add_argument("--query", help="FTS query the drawer must match")
    di.add_argument("--since", help="drawer updated_at >= this ISO date")
    di.add_argument("--until", help="drawer updated_at <= this ISO date")
    di.add_argument("--include-related-depth", type=int, default=0,
                    help="expand matches by N hops via the relations graph")
    di.add_argument("--activate", action="store_true",
                    help="after writing, rename current brain.db to .bak-TIMESTAMP "
                         "and rename the new file to brain.db")

    ar = sub.add_parser("archive")
    ar.add_argument("--output", required=True, help="path for the archive brain.db")
    ar.add_argument("--older-than-days", type=int, default=180,
                    help="archive drawers untouched for at least N days (default 180)")
    ar.add_argument("--before", help="archive drawers with updated_at <= this ISO date")
    ar.add_argument("--tag", action="append", help="archive drawers with this tag (repeatable)")
    ar.add_argument("--collection", help="archive drawers in this collection")
    ar.add_argument("--dry-run", action="store_true", help="show counts without writing")

    mb = sub.add_parser("merge-brain")
    mb.add_argument("--from", dest="source", required=True,
                    help="path to a brain.db whose drawers will be merged into the working brain")

    args = p.parse_args()
    b = SecondBrain(args.db) if args.db else SecondBrain()

    def out(obj, human):
        if args.json:
            print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
        else:
            print(human)

    try:
        if args.cmd == "add":
            if args.content_file:
                content = Path(args.content_file).read_text(encoding="utf-8")
            else:
                if args.content is None:
                    sys.exit("❌ add needs either a content argument or --content-file")
                content = args.content
            tags = [x.strip() for x in (args.tags or "").split(",") if x.strip()]
            dr = b.add(args.title, content, args.collection, tags, args.source or [])
            links = b.related(dr["id"], source="wikilink")
            msg = f"✅ Saved \"{dr['title']}\"  {dr['id'][:8]}"
            if links:
                msg += "\n   → linked: " + ", ".join(x["title"] for x in links)
            pend = [pl for pl in b.con.execute(
                "SELECT target_title FROM pending_links WHERE from_id=?", (dr["id"],))]
            if pend:
                msg += "\n   ⏳ pending: " + ", ".join(x[0] for x in pend)
            out(dr, msg)

        elif args.cmd == "search":
            res = b.search(args.query, args.collection, args.tag, args.limit)
            human = f"📚 {len(res)} results\n\n" + "\n\n".join(
                _fmt_drawer_line(i + 1, d) for i, d in enumerate(res)) if res \
                else "No results. Try broader terms or check the collection/tag filter."
            out(res, human)

        elif args.cmd == "show":
            matches = [b.get(args.ident)] if b.get(args.ident) else b.get_by_title(args.ident)
            matches = [m for m in matches if m]
            if not matches:
                out(None, f"No live drawer matches '{args.ident}'.")
            elif len(matches) > 1:
                human = f"⚠️ {len(matches)} drawers match '{args.ident}':\n\n" + "\n\n".join(
                    _fmt_drawer_line(i + 1, d) for i, d in enumerate(matches)) + \
                    "\n\nRe-run with the 8-char id to pick one."
                out(matches, human)
            else:
                dd = matches[0]
                rels = b.related(dd["id"])
                human = (f"# {dd['title']}\n"
                         f"Collection: {dd['collection'] or '(none)'}   Tags: {', '.join(dd['tags']) or '—'}\n"
                         f"Sources: {', '.join(dd['sources']) or '—'}\n"
                         f"Updated: {dd['updated_at']}   ID: {dd['id']}\n\n{dd['content']}\n")
                if rels:
                    human += "\n🔗 Relations:\n" + "\n".join(
                        f"   [{r['dir']}|{r['relation_type']}|{r['source']}] {r['title']} ({r['id'][:8]})"
                        for r in rels)
                out(dd, human)

        elif args.cmd == "update":
            tags = [x.strip() for x in args.tags.split(",")] if args.tags is not None else None
            dr = b.update(args.id, args.title, args.content, tags, args.collection)
            out(dr, f"✅ Updated {args.id[:8]}" if dr else f"No live drawer {args.id[:8]}")

        elif args.cmd == "delete":
            ok = b.delete(args.id, args.hard)
            kind = "hard-deleted (permanent)" if args.hard else "soft-deleted (recover with restore)"
            out({"ok": ok}, f"{'🗑️ ' + kind if ok else 'Nothing to delete'}: {args.id[:8]}")

        elif args.cmd == "restore":
            ok = b.restore(args.id)
            out({"ok": ok}, f"♻️ Restored {args.id[:8]}" if ok else f"Nothing to restore: {args.id[:8]}")

        elif args.cmd == "list":
            res = b.list(args.collection, args.tag, args.limit, args.offset, args.sort)
            human = "\n\n".join(_fmt_drawer_line(i + 1, d) for i, d in enumerate(res)) or "Empty."
            out(res, human)

        elif args.cmd == "collections":
            cs = b.collections()
            human = f"📂 Collections ({len(cs)})\n\n" + "\n".join(
                f"{c['name']:<14} — {c['n']} drawers" for c in cs)
            out(cs, human)

        elif args.cmd == "tags":
            ts = b.tags(args.sort, args.limit)
            human = "\n".join(f"{t['name']:<20} ×{t['n']}" for t in ts) or "No tags."
            out(ts, human)

        elif args.cmd == "relate":
            try:
                rid = b.relate(args.from_id, args.to_id, args.type, args.strength)
                out({"id": rid}, f"🔗 {args.from_id[:8]} —{args.type}→ {args.to_id[:8]}")
            except ValueError as ex:
                out({"error": str(ex)}, f"❌ {ex}")

        elif args.cmd == "related":
            rs = b.related(args.id, args.limit, args.source)
            human = "\n".join(
                f"[{r['dir']}|{r['relation_type']}|{r['source']}] {r['title']} ({r['id'][:8]})"
                for r in rs) or "No relations."
            out(rs, human)

        elif args.cmd == "traverse":
            ns = b.traverse(args.id, args.depth, args.limit)
            human = "\n".join(f"hop {n['hop']}: {n['title']} ({n['id'][:8]})" for n in ns) or "No reachable nodes."
            out(ns, human)

        elif args.cmd == "export":
            if args.format == "markdown" and args.output:
                try:
                    res = b.export_vault(args.output, args.collection)
                except Exception as ex:
                    out({"error": str(ex)}, f"❌ {ex}")
                    sys.exit(1)
                print(f"💾 Exported {res['drawers']} notes → {res['path']}/")
            else:
                data = b.export(args.collection, args.format)
                if args.output:
                    Path(args.output).write_text(data, encoding="utf-8")
                    print(f"💾 Exported to {args.output}")
                else:
                    print(data)

        elif args.cmd == "import":
            mode = "replace" if args.replace else "merge"
            res = b.import_(args.path, mode)
            out(res, f"📥 Imported: {res['added']} added, {res['skipped']} skipped ({mode})")

        elif args.cmd == "stats":
            s = b.stats(args.collection)
            top_tags = ", ".join(t["name"] + "×" + str(t["n"]) for t in s["tags"]) or "none"
            colls = ", ".join(c["name"] + "(" + str(c["n"]) + ")" for c in s["collections"])
            human = (f"📊 SecondBrain\n\n"
                     f"Drawers: {s['drawers']} ({s['uncollected']} uncollected, "
                     f"{s['soft_deleted']} soft-deleted)\n"
                     f"Relations: {sum(s['relations'].values())} "
                     f"({', '.join(str(k) + ': ' + str(v) for k, v in s['relations'].items()) or 'none'})\n"
                     f"Pending links: {s['pending_links']}\n"
                     f"Top tags: {top_tags}\n"
                     f"Collections: {colls}")
            out(s, human)

        elif args.cmd == "summary":
            s = b.summary(cold_threshold_days=args.cold_days)
            d = s["drawers"]
            rels = s["relations"]
            rec_lines = []
            if s["recommendation"] == "archive":
                rec_lines = [
                    "",
                    "💡 Recommendation: archive",
                    f"   You have {d['cold']} cold drawers (untouched "
                    f"{d['cold_threshold_days']}+ days, {d['cold']*100//max(d['alive'],1)}% of total).",
                    "   Run: brain archive --output ~/.secondbrain/archive-$(date +%F).db",
                ]
            elif s["recommendation"] == "archive-then-distill":
                rec_lines = [
                    "",
                    "💡 Recommendation: archive then distill",
                    f"   Brain is {s['size_human']} with {d['cold']} cold drawers. "
                    "Archive first, then distill a focused working copy.",
                ]
            human = (
                f"🧠 SecondBrain summary\n\n"
                f"  Path:     {s['db_path']}\n"
                f"  Size:     {s['size_human']}\n"
                f"  Drawers:  {d['alive']:,} alive   {d['cold']:,} cold ({d['cold_threshold_days']}d+)   "
                f"{d['soft_deleted']:,} soft-deleted\n"
                f"  Relations: {sum(rels.values()):,} total   "
                f"({', '.join(f'{k}×{v}' for k, v in rels.items()) or 'none'})\n"
                f"  Pending:  {s['pending_links']:,} unresolved wikilinks"
            ) + "\n".join(rec_lines)
            out(s, human)

        elif args.cmd == "distill":
            tags = args.tag or []
            try:
                res = b.distill(args.output, tags=tags or None,
                                collection=args.collection, query=args.query,
                                since=args.since, until=args.until,
                                include_related_depth=args.include_related_depth)
            except (ValueError, FileExistsError) as ex:
                out({"error": str(ex)}, f"❌ {ex}")
                sys.exit(1)
            if res["drawers"] == 0:
                out(res, f"⚠ No drawers matched. Nothing written to {res['path']}.")
                sys.exit(0)

            if args.activate:
                working = b.db_path.resolve()
                new_path = Path(args.output).resolve()
                if new_path == working:
                    sys.exit("❌ --output is the same as the working brain; refusing to swap.")
                if new_path.parent != working.parent:
                    target = working.parent / new_path.name
                    if target.exists():
                        sys.exit(f"❌ {target} already exists; pick a different --output.")
                    os.replace(new_path, target)
                    new_path = target

                ts = datetime.now().strftime("%Y%m%dT%H%M%S")
                backup = working.with_suffix(f".db.bak-{ts}")
                b.checkpoint_and_close()
                os.replace(working, backup)
                os.replace(new_path, working)
                human = (
                    f"✨ Distilled {res['drawers']:,} drawers → {working}\n"
                    f"   tags:        {res['tags']:,}\n"
                    f"   relations:   {res['relations']:,}\n"
                    f"   pending:     {res['pending_links']:,}\n"
                    f"   old brain →  {backup}\n"
                    f"   new working: {working}\n"
                    f"   old brain is now a point-in-time backup; restore with `cp {backup} {working}`"
                )
                out({"distill": res, "backup": str(backup), "activated": str(working)}, human)
                sys.exit(0)

            human = (
                f"✨ Distilled {res['drawers']:,} drawers → {args.output}\n"
                f"   tags:      {res['tags']:,}\n"
                f"   relations: {res['relations']:,}\n"
                f"   pending:   {res['pending_links']:,}\n"
                f"\n   (working brain untouched. Use --activate to swap.)"
            )
            out(res, human)

        elif args.cmd == "archive":
            tags = args.tag or []
            try:
                res = b.archive(args.output, older_than_days=args.older_than_days,
                                before_date=args.before, tags=tags or None,
                                collection=args.collection, dry_run=args.dry_run)
            except (ValueError, FileExistsError) as ex:
                out({"error": str(ex)}, f"❌ {ex}")
                sys.exit(1)
            if args.dry_run:
                human = (
                    f"🔍 Dry run — no files written\n"
                    f"   would archive:  {res['would_archive']:,} drawers (criterion: {res['criterion']})\n"
                    f"   would remain:   {res['would_remain']:,} drawers\n"
                    f"   target file:    {res['path']}"
                )
            elif res["archived"] == 0:
                human = f"⚠ No drawers matched ({res['criterion']}). Nothing archived."
            else:
                human = (
                    f"🗄️  Archived {res['archived']:,} drawers → {res['path']}\n"
                    f"   criterion:   {res['criterion']}\n"
                    f"   relations:   {res['archived_relations']:,} archived with their drawers\n"
                    f"   remaining:   {res['remaining']:,} drawers in working brain\n"
                    f"   new size:    {res['size_remaining_human']}\n"
                    f"\n   to bring a drawer back: brain merge-brain --from {res['path']}"
                )
            out(res, human)

        elif args.cmd == "merge-brain":
            try:
                res = b.merge_brain(args.source)
            except FileNotFoundError as ex:
                out({"error": str(ex)}, f"❌ {ex}")
                sys.exit(1)
            human = (
                f"🔀 Merged from {res['source_path']}\n"
                f"   drawers added:        {res['drawers_added']:,}  "
                f"(skipped: {res['drawers_skipped']:,} already present)\n"
                f"   tag links added:      {res['tag_links_added']:,}\n"
                f"   relations added:      {res['relations_added']:,}\n"
                f"   pending links added:  {res['pending_links_added']:,}\n"
                f"\n   wikilinks re-derived for new drawers; cross-refs auto-resolved."
            )
            out(res, human)

    finally:
        b.close()


if __name__ == "__main__":
    main()
