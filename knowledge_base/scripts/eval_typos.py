"""Misspelled queries: (query, entity name expected among the top-3 results). Measures what fuzzy matching buys;
the other evals and the tldr benchmark contain almost no typos. Written before looking at results."""
from search import search
CASES = [
 ("namspaces", "namespaces"), ("secomp", "seccomp"), ("seccmop", "seccomp"), ("ptarce", "ptrace"), ("strcae", "strace"),
 ("tcpdmup", "tcpdump"), ("iptabels", "iptables"), ("nmpa", "nmap"), ("hascat", "hashcat"), ("sqlmpa", "sqlmap"),
 ("gobustr", "gobuster"), ("landlok", "landlock"), ("capabilites", "capabilities"), ("cgroup", "cgroups"),
 ("userfaultd", "userfaultfd"), ("memfd create", "memfd_create"), ("bpftrce", "bpftrace"), ("radere2", "radare2"),
 ("suricta", "suricata"), ("objdmp", "objdump"), ("readlef", "readelf"), ("opnessl", "openssl"), ("wireshark tshrak", "tshark"),
 ("socta", "socat"), ("massscan", "masscan"), ("yara rule", "yara"), ("auditcl", "auditctl"), ("keyctrl", "keyctl"),
 ("perf event open", "perf_event_open"), ("chrot", "chroot"),
 ("nmap -sVV", "nmap"), ("tcpdump -nni", "tcpdump"), ("strace -ff -oo", "strace"),
 ("sql injectoin", "SQL Injection"), ("cross site scriptng", "Cross-site Scripting"), ("use after fre", "Use After Free"),
 ("proccess hollowing", "Process Hollowing"), ("kerberosting", "Kerberoasting"), ("pass the hsh", "Pass the Hash"),
 ("lsass memroy dump", "LSASS Memory"), ("powershel execution", "PowerShell"), ("path travresal", "Path Traversal"),
 ("buffer overlfow", "Buffer"), ("race condtion", "Race Condition"), ("log4shell CVE-2021-4428", "Log4j"),
 ("T1055.01", "Process Injection"), ("CWE79", "Cross-site Scripting"), ("cve 2014 0160", "OpenSSL"),
]
def main():
    ok = 0
    for q, exp in CASES:
        res = search(q, limit=3)
        hit = any(exp.lower() in (r[0][1] + " " + r[0][5][:300]).lower() for r in res)
        ok += hit
        print(f"{'PASS' if hit else 'FAIL'}  {q!r:28s} -> {res[0][0][1][:40] if res else '-'}")
    print(f"\ntypos top-3: {ok}/{len(CASES)}")
main()
