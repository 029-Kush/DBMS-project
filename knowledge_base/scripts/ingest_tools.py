"""Ingest man pages, --help output and tldr examples for an allowlist of tools.
import os
Only allowlisted binaries are executed, with timeout, no stdin, no sudo."""
import hashlib, re, shutil, subprocess, sys
from pathlib import Path
from common import *

# name -> domains. GUI tools (burpsuite, wireshark) are deliberately excluded:
# --help would launch a window. Missing binaries are recorded as skipped.
TOOLS = {
 "nmap": ["red"], "ncat": ["red"], "nc": ["red"], "socat": ["red"], "hydra": ["red"],
 "hashcat": ["red"], "john": ["red"], "sqlmap": ["red"], "nikto": ["red"], "ffuf": ["red"],
 "gobuster": ["red"], "masscan": ["red"], "tshark": ["blue", "red"], "tcpdump": ["blue", "red"],
 "gdb": ["systems", "red"], "strace": ["systems"], "ltrace": ["systems"], "objdump": ["systems"],
 "readelf": ["systems"], "strings": ["systems"], "openssl": ["systems", "red", "blue"],
 "curl": ["systems"], "ssh": ["systems"], "dig": ["blue"], "ss": ["blue", "systems"],
 "nft": ["blue"], "iptables": ["blue"], "yara": ["blue"], "suricata": ["blue"],
 "bpftrace": ["blue", "systems"], "binwalk": ["red"], "radare2": ["red"], "semgrep": ["blue"],
 "getcap": ["systems"], "setcap": ["systems"], "auditctl": ["blue"],
}
MAXCH = 1500
HEAD_RE = re.compile(r"^[A-Z][A-Z0-9 ,&/()-]{2,}$")

def run(cmd, timeout=5):
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=timeout, env={"PATH": os.environ["PATH"], "LANG": "C", "MANWIDTH": "100", "TERM": "dumb"})
        return (p.stdout or "") + ("" if p.stdout else (p.stderr or ""))
    except Exception:
        return ""

def pkg_version(binpath):
    out = run(["rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}", "--whatprovides", binpath])
    return out.strip() if out and "no package" not in out else None

OPT_START = re.compile(r"^\s*-{1,2}[A-Za-z0-9?]")

def split_blocks(text, limit=MAXCH, by_option=False):
    """Group blank-line separated paragraphs into chunks <= limit chars.
    by_option: start a new chunk at every option-definition paragraph (-x / --long),
    so one man-page option == one chunk and exact flag lookups land on its definition."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        new_opt = by_option and OPT_START.match(p) and cur
        if cur and (new_opt or len(cur) + len(p) + 2 > limit):
            chunks.append(cur); cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur: chunks.append(cur)
    out = []
    for c in chunks:                       # hard-split any single oversized paragraph on lines
        while len(c) > limit * 2:
            cut = c.rfind("\n", 0, limit * 2) or limit * 2
            out.append(c[:cut]); c = c[cut:]
        out.append(c)
    return out

def man_sections(text):
    sec, buf, res = "overview", [], []
    for line in text.splitlines():
        if HEAD_RE.match(line.strip()) and not line.startswith(" "):
            if buf: res.append((sec, "\n".join(buf)))
            sec, buf = line.strip().lower(), []
        else:
            buf.append(line.rstrip())
    if buf: res.append((sec, "\n".join(buf)))
    return res

def tldr_page(name):
    for plat in ("common", "linux"):
        p = HDD / "sources" / "tldr-pages" / "pages" / plat / f"{name}.md"
        if p.exists(): return p.read_text()
    return None

def main(names):
    conn = connect(); cur = conn.cursor()
    stats = {"tools": 0, "skipped": [], "chunks": 0, "dupes": 0}
    for name in names:
        path = shutil.which(name)
        man = run(["man", "-P", "cat", name], 10) if shutil.which("man") else ""
        man = re.sub(r".\x08", "", man)   # strip overstrike
        tl = tldr_page(name)
        help_txt = run([path, "--help"]) if path else ""
        if not (man.strip() or tl or help_txt.strip()):
            stats["skipped"].append(name); continue
        ver = pkg_version(path) if path else None
        cur.execute("""INSERT INTO entities(name,type,domain,version) VALUES (%s,'tool',%s,%s)
                       ON CONFLICT (type,name,ext_id) DO UPDATE SET version=EXCLUDED.version, domain=EXCLUDED.domain
                       RETURNING id""", (name, TOOLS.get(name, []), ver))
        eid = cur.fetchone()[0]
        items = []   # (section, source, trust, is_code, text)
        for sec, body in man_sections(man) if man.strip() else []:
            for part in split_blocks(body, by_option=True):
                if len(part.strip()) > 20: items.append((sec, "man", 1, False, part.strip()))
        if help_txt.strip() and len(help_txt) > 40:
            for part in split_blocks(help_txt): items.append(("help", "help", 1, False, part.strip()))
        if tl:
            for part in split_blocks(tl, 1200): items.append(("examples", "tldr", 2, True, part.strip()))
        texts = [f"{name} {s}: {t}" for s, _, _, _, t in items]
        vecs = embed(texts) if texts else []
        for (sec, src, trust, code, body), v in zip(items, vecs):
            h = hashlib.sha256(f"{name}|{sec}|{body}".encode()).hexdigest()
            n = store_chunk(cur, eid, sec, src, ver, trust, code, body, extract_flags(body), h, v)
            stats["chunks"] += n; stats["dupes"] += 1 - n
        stats["tools"] += 1
        conn.commit()
        print(f"{name:10s} chunks={len(items):4d} ver={ver}", flush=True)
    print(stats)

if __name__ == "__main__":
    import os
    main(sys.argv[1:] or list(TOOLS))
