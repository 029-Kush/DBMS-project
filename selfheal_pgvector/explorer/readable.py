"""Turn a stored chunk into display blocks: [{"t": "p"|"li"|"code"|"kv", "s": text, "k": label}].
Man pages and kernel docs are hard-wrapped with doubled spaces; this re-flows prose into paragraphs and keeps
indented examples verbatim. The stored text is never altered, only how it is shown."""
import re

STRUCTURED = {"attack", "cwe", "kev"}          # already one idea per line: keep line breaks
BULLET = re.compile(r"^(?:[o*•-]\s{1,}|\d{1,2}[.)]\s+)(?=\S)")
KV = re.compile(r"^([A-Z][A-Za-z/ ()-]{1,28}): (\S.*)$")
CODE = re.compile(r"[{};]\s*$|^\s*[$#]\s|\\\s*$|/\*|\*/|->|\w\(.*\);|^\s*(struct|int|static|void|#include)\b")
CMD = re.compile(r"^(sudo\s+)?[a-z][\w./-]*\s+(-{1,2}[A-Za-z0-9][\w=.:/,@-]*|[\w.-]+\.[a-z]{2,}\S*)(\s+\S+)*$")   # "nmap -sS host", "dig +short x.org"
MAN_HEAD = re.compile(r"^\S+\(\d\w*\)\s{2,}.*\S+\(\d\w*\)\s*$|^(Linux|GNU|Debian)\b.*\(\d\w*\)\s*$")

def _paragraph(lines):
    out = []
    for ln in lines:
        s = ln.strip()
        if out and out[-1].endswith("-") and s[:1].islower():   # "num-" + "ber"
            out[-1] = out[-1][:-1] + s if out[-1][-2:-1].isalpha() and len(out[-1].rsplit(" ", 1)[-1]) <= 4 else out[-1] + s
        else:
            out.append(s)
    return re.sub(r"\s+", " ", " ".join(out)).strip()

def blocks(body, source=""):
    body = body.expandtabs(8)
    lines = [l.rstrip() for l in body.split("\n")
             if not MAN_HEAD.match(l.strip()) and not re.fullmatch(r"[=\-~^\"*#+]{3,}", l.strip())]   # man header, rst underlines
    res = []
    if source in STRUCTURED:
        for l in lines:
            s = l.strip()
            if not s: continue
            if not res and re.match(r"^(T\d{4}|CWE-\d+|CVE-\d|[MGSC]\d{4})", s):   # "T1055 Process Injection" header line
                res.append({"t": "term", "s": s}); continue
            if BULLET.match(s) or s.startswith("- "):
                res.append({"t": "li", "s": BULLET.sub("", s) if BULLET.match(s) else s[2:]})
            else:
                m = KV.match(s)
                res.append({"t": "kv", "k": m.group(1), "s": m.group(2)} if m and len(s) < 400 else {"t": "p", "s": s})
        return res
    nonblank = [l for l in lines if l.strip()]
    if not nonblank: return []
    # ingestion strip()ed the chunk's first line, so it lost the indent its wrapped continuation lines still have
    first_i = next(i for i, l in enumerate(lines) if l.strip())
    rest = []
    for l in lines[first_i + 1:]:
        if not l.strip(): break
        rest.append(len(l) - len(l.lstrip()))
    if rest and lines[first_i] == lines[first_i].lstrip() and min(rest) > 0:
        lines[first_i] = " " * min(rest) + lines[first_i]
        nonblank = [l for l in lines if l.strip()]
    base = min(len(l) - len(l.lstrip()) for l in nonblank)
    group, groups = [], []
    for l in lines + [""]:
        if l.strip(): group.append(l[base:] if len(l) >= base else l.lstrip())
        elif group: groups.append(group); group = []
    for gi, g in enumerate(groups):
        ind = [len(l) - len(l.lstrip()) for l in g]
        first = ind[0]
        if g[0].rstrip().endswith("::") and len(g) == 1:       # rst "Usage::" lead-in
            res.append({"t": "p", "s": g[0].rstrip()[:-2].strip() + ":"}); continue
        if any(CODE.search(l) for l in g) or (len(g) <= 3 and all(CMD.match(l.strip()) and len(l.strip()) < 140
                                                                   and not l.strip().endswith(".") for l in g)):
            cut = min(ind)
            res.append({"t": "code", "s": "\n".join(l[cut:] for l in g)}); continue
        if (len(g) > 1 and first < min(ind[1:]) and len(g[0].strip()) <= 70 and not g[0].strip().endswith((".", ","))
                and not BULLET.match(g[0].strip())):             # "TERM" line directly followed by its indented definition
            res.append({"t": "term", "s": g[0].strip()})
            g, ind = g[1:], ind[1:]; first = ind[0]
        text = _paragraph(g)
        nxt = groups[gi + 1][0] if gi + 1 < len(groups) else None
        if (len(g) == 1 and len(text) <= 70 and not text.endswith((".", ",")) and nxt is not None
                and len(nxt) - len(nxt.lstrip()) > first):
            res.append({"t": "term", "s": text}); continue
        if BULLET.match(g[0].strip()):
            item = []
            for l in g:
                if BULLET.match(l.strip()) and item:
                    res.append({"t": "li", "s": _paragraph([BULLET.sub("", item[0].strip())] + item[1:])}); item = []
                item.append(l)
            res.append({"t": "li", "s": _paragraph([BULLET.sub("", item[0].strip())] + item[1:])})
        else:
            res.append({"t": "p", "s": text, "ind": first > 0})
    for b in res:
        if b["t"] != "code":
            b["s"] = b["s"].replace("``", "`")
    return res

def snippet(bs, n=150):
    for b in bs:
        if b["t"] in ("p", "kv", "li") and len(b["s"]) > 25:
            return b["s"][:n] + ("…" if len(b["s"]) > n else "")
    return bs[0]["s"][:n] if bs else ""


# ---- topic page: every chunk of one entity, grouped and ordered like a reference page ------------------
ORDER = [  # (rank, group label, matches the part of `section` after "page(n): ")
    (0, "What it is", r"^(name|overview|introduction|summary)$"),
    (1, "Usage and syntax", r"^(synopsis|usage)$"),
    (2, "Examples and commands", r"^(examples?|example runs?|examples? usage)$"),
    (3, "How it works", r"^(description|how it works|what it isn't)$"),
    (5, "Detection", r"^detection"),
    (5, "Mitigations", r"^mitigations?$"),
    (5, "Weaknesses and consequences", r"^(consequences|common consequences|related weaknesses|potential mitigations)$"),
    (6, "Notes, caveats, pitfalls", r"^(notes|caveats|pitfalls|bugs|errors|return values?|sysctls)$"),
    (7, "See also", r"^see also$"),
]
def _rank(sec):
    part = sec.split(": ", 1)[-1].lower()
    for rank, label, pat in ORDER:
        if re.match(pat, part): return rank, label
    if part in ("help", "options summary") or part.endswith("options"): return 4, "Options and flags"
    if part.startswith(("procedure examples", "techniques used")): return 5, "Used by / examples in the wild"
    return 4, "Details: " + part

def chunk_blocks(section, source, body):
    """Display blocks for one chunk, aware of the source's formatting (tldr markdown, --help output, synopsis)."""
    part = section.split(": ", 1)[-1].lower()
    if source == "help" or part == "synopsis":
        ls = re.sub(r"[ \t]+\n", "\n", body.expandtabs(8)).strip("\n").split("\n")
        rest = [len(l) - len(l.lstrip()) for l in ls[1:] if l.strip()]
        cut = min(rest) if rest and ls[0] == ls[0].lstrip() else 0       # first line was strip()ed at ingest; realign the rest
        return [{"t": "code", "s": "\n".join([ls[0]] + [l[cut:] if not l[:cut].strip() else l for l in ls[1:]])}]
    if source == "tldr":
        out = []
        for l in body.split("\n"):
            s = l.strip()
            if not s or s.startswith("#"): continue
            if s.startswith("`") and s.endswith("`") and len(s) > 2: out.append({"t": "code", "s": s.strip("`")})
            elif s.startswith(">"): out.append({"t": "p", "s": s.lstrip("> ")})
            else: out.append({"t": "p", "s": re.sub(r"^-\s*", "", s)})
        return out
    return blocks(body, source)

def topic(entity, chunks):
    """entity: dict(name,type,version,aliases); chunks: [(id, section, source, source_version, body)] in id order."""
    groups = {}
    for cid, sec, src, ver, body in chunks:
        bs = chunk_blocks(sec, src, body)
        if not bs: continue
        rank, label = _rank(sec)
        g = groups.setdefault(label, {"label": label, "rank": rank, "items": []})
        g["items"].append({"id": cid, "page": sec.split(": ", 1)[0] if ": " in sec else "", "source": src, "version": ver, "blocks": bs})
    out = sorted(groups.values(), key=lambda g: (g["rank"], g["label"]))
    return {"name": entity["name"], "type": entity["type"], "aliases": entity["aliases"], "groups": out,
            "chunks": sum(len(g["items"]) for g in out)}
