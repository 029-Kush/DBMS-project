"""Ingest Linux security concepts (syscalls, kernel mechanisms) from the installed man pages (sections 2/5/7)
and the local kernel tree's Documentation/*.rst. One entity per concept (type='concept'); chunks keep the
man page / doc they came from in `section` ("seccomp(2): description").
Provenance: man-pages package version via rpm, kernel version from ~/linux/Makefile."""
import hashlib, re, shutil, sys
from pathlib import Path
from common import *
from ingest_tools import run, split_blocks, man_sections

KERNEL = Path.home() / "linux"
DOCS = "Documentation/userspace-api/"
# concept -> (domains, aliases, man pages "sec name", kernel docs relative to Documentation/)
CONCEPTS = {
 "seccomp":      (["systems", "blue"], ["seccomp-bpf", "seccomp filter"], ["2 seccomp", "2 seccomp_unotify"], ["userspace-api/seccomp_filter.rst"]),
 "landlock":     (["systems", "blue"], [], ["7 landlock", "2 landlock_create_ruleset", "2 landlock_add_rule", "2 landlock_restrict_self"], ["userspace-api/landlock.rst"]),
 "capabilities": (["systems", "red", "blue"], ["linux capabilities", "cap_sys_admin"], ["7 capabilities", "7 credentials"], []),
 "namespaces":   (["systems", "red", "blue"], ["linux namespaces", "user namespaces", "pid namespaces", "mount namespaces", "network namespaces"],
                  ["7 namespaces", "7 user_namespaces", "7 pid_namespaces", "7 mount_namespaces", "7 network_namespaces",
                   "7 cgroup_namespaces", "2 unshare", "2 setns", "2 clone"], []),
 "cgroups":      (["systems", "blue"], ["cgroup", "control groups"], ["7 cgroups"], []),
 "bpf":          (["systems", "blue", "red"], ["ebpf", "bpf syscall"], ["2 bpf"], ["userspace-api/ebpf/syscall.rst"]),
 "ptrace":       (["systems", "red"], [], ["2 ptrace"], []),
 "prctl":        (["systems"], ["pr_set_seccomp", "pr_set_no_new_privs", "no_new_privs"], ["2 prctl"], []),
 "lsm":          (["systems", "blue"], ["linux security modules"], ["7 selinux", "7 apparmor"], ["userspace-api/lsm.rst"]),
 "memfd_create": (["systems", "red"], ["memfd"], ["2 memfd_create"], []),
 "userfaultfd":  (["systems", "red"], [], ["2 userfaultfd"], []),
 "perf_event_open": (["systems", "red"], ["perf events"], ["2 perf_event_open"], []),
 "keyctl":       (["systems"], ["kernel keyring"], ["2 keyctl"], []),
 "chroot":       (["systems", "red"], ["pivot_root"], ["2 chroot", "2 pivot_root"], []),
 "mount":        (["systems"], [], ["2 mount"], []),
 "execve":       (["systems", "red"], ["exec"], ["2 execve"], []),
 "pkeys":        (["systems"], ["memory protection keys"], ["7 pkeys"], []),
 "syscalls":     (["systems"], ["system calls"], ["2 syscalls"], []),
}
HDR = re.compile(r"^(?P<t>\S.*)\n(?P<u>[=\-~^\"`#*+]{3,})\s*$", re.M)

def rst_sections(text):
    """Split on rst headings (a title line underlined with repeated punctuation)."""
    marks = [(m.start(), m.end(), m.group("t").strip()) for m in HDR.finditer(text) if len(m.group("u")) >= len(m.group("t").strip())]
    if not marks: return [("overview", text)]
    out = [("overview", text[:marks[0][0]])] if text[:marks[0][0]].strip() else []
    for i, (_, end, title) in enumerate(marks):
        out.append((title.lower(), text[end: marks[i + 1][0] if i + 1 < len(marks) else len(text)]))
    return out

def main(names):
    conn = connect(); cur = conn.cursor()
    kver = "linux-" + ".".join(re.search(rf"^{k} = (\S+)", (KERNEL / "Makefile").read_text(), re.M).group(1) for k in ("VERSION", "PATCHLEVEL")) \
        if (KERNEL / "Makefile").exists() else None
    mver = run(["rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}", "man-pages"]).strip() or None
    stats = {"concepts": 0, "chunks": 0, "missing": []}
    for name in names:
        domains, aliases, mans, rsts = CONCEPTS[name]
        eid = upsert_entity(cur, "concept", name, domain=domains, aliases=aliases, version=mver)
        n = 0
        for spec in mans:
            sec_no, page = spec.split()
            text = re.sub(r".\x08", "", run(["man", "-P", "cat", sec_no, page], 15))
            if not text.strip() or text.startswith("No manual entry"): stats["missing"].append(spec); continue
            items = [(f"{page}({sec_no}): {s}", p.strip()) for s, b in man_sections(text) for p in split_blocks(b)]
            n += add_chunks(cur, eid, name, items, "man", mver, trust=1)
        for rel in rsts:
            f = KERNEL / "Documentation" / rel
            if not f.exists(): stats["missing"].append(rel); continue
            items = [(f"{f.name}: {s}", p.strip()) for s, b in rst_sections(f.read_text()) for p in split_blocks(b)]
            n += add_chunks(cur, eid, name, items, "kernel-doc", kver, trust=1)
        conn.commit(); stats["concepts"] += 1; stats["chunks"] += n
        print(f"{name:16s} +{n} chunks", flush=True)
    print(stats)

if __name__ == "__main__":
    main(sys.argv[1:] or list(CONCEPTS))
