"""Tiny local web UI over search.search(). Stdlib only; binds 127.0.0.1."""
import html, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from search import search

PAGE = """<!doctype html><meta charset=utf-8><title>security-kb</title>
<style>body{{font:15px system-ui;max-width:900px;margin:2em auto;padding:0 1em;background:#111;color:#ddd}}
input{{width:80%;padding:.5em;font-size:16px}}button{{padding:.5em 1em}}
.r{{border:1px solid #333;border-radius:6px;margin:1em 0;padding:.7em}}
.h{{color:#8cf;font-weight:600}}.m{{color:#888;font-size:12px}}pre{{white-space:pre-wrap;font:13px ui-monospace,monospace}}</style>
<form><input name=q value="{q}" autofocus placeholder="e.g. tar -C, find files changed today">
<button>Search</button></form>{body}"""

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query).get("q", [""])[0].strip()
        out = ""
        if q:
            for (cid, ent, sec, src, ver, body), sc, via in search(q, limit=8):
                out += (f'<div class=r><div class=h>{html.escape(ent)} · {html.escape(sec)}</div>'
                        f'<div class=m>{html.escape(src)} · {html.escape(str(ver))} · rrf {sc:.4f} via {",".join(via)}</div>'
                        f'<pre>{html.escape(body)}</pre></div>')
        data = PAGE.format(q=html.escape(q, quote=True), body=out).encode()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), H).serve_forever()
