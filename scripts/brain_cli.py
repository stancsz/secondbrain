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

Output is human-readable. Pass --json to any read command for machine output.
"""
import argparse
import json
import sys
from brain import SecondBrain


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

    a = sub.add_parser("add"); a.add_argument("title"); a.add_argument("content")
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

    args = p.parse_args()
    b = SecondBrain(args.db) if args.db else SecondBrain()

    def out(obj, human):
        if args.json:
            print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
        else:
            print(human)

    if args.cmd == "add":
        tags = [x.strip() for x in (args.tags or "").split(",") if x.strip()]
        dr = b.add(args.title, args.content, args.collection, tags, args.source or [])
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
        data = b.export(args.collection, args.format)
        if args.output:
            from pathlib import Path
            Path(args.output).write_text(data)
            print(f"💾 Exported to {args.output}")
        else:
            print(data)

    elif args.cmd == "import":
        mode = "replace" if args.replace else "merge"
        res = b.import_(args.path, mode)
        out(res, f"📥 Imported: {res['added']} added, {res['skipped']} skipped ({mode})")

    elif args.cmd == "stats":
        s = b.stats(args.collection)
        human = (f"📊 SecondBrain\n\n"
                 f"Drawers: {s['drawers']} ({s['uncollected']} uncollected, "
                 f"{s['soft_deleted']} soft-deleted)\n"
                 f"Relations: {sum(s['relations'].values())} "
                 f"({', '.join(f'{k}: {v}' for k,v in s['relations'].items()) or 'none'})\n"
                 f"Pending links: {s['pending_links']}\n"
                 f"Top tags: {', '.join(f'{t['name']}×{t['n']}' for t in s['tags']) or 'none'}\n"
                 f"Collections: {', '.join(f'{c['name']}({c['n']})' for c in s['collections'])}")
        out(s, human)

    b.close()


if __name__ == "__main__":
    main()
