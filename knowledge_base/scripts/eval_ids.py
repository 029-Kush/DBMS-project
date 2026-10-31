"""Checks for ATT&CK / CWE / KEV lookups: (query, substring expected in top-3 bodies). Written before looking at results."""
from search import search
CASES = [
 ("T1055.012", "Process Hollowing"), ("T1003.001", "LSASS"), ("T1059.001", "PowerShell"), ("T1566.001", "Attachment"),
 ("T1021.001", "Remote Desktop"), ("T1486", "Data Encrypted for Impact"), ("T1078", "Valid Accounts"), ("T1190", "Public-Facing"),
 ("CWE-79", "Cross-site Scripting"), ("CWE-787", "Out-of-bounds Write"), ("CWE-89", "SQL Injection"), ("CWE-22", "Path Traversal"),
 ("CWE-416", "Use After Free"), ("CWE-502", "Deserialization"),
 ("CVE-2021-44228", "Log4j"), ("CVE-2017-0144", "SMB"), ("CVE-2021-34527", "Print Spooler"), ("CVE-2014-0160", "OpenSSL"),
 ("credential dumping from LSASS memory", "T1003.001"), ("dll injection into another process", "T1055.001"),
 ("pass the hash", "T1550.002"), ("ransomware encrypts files for impact", "T1486"), ("kerberoasting", "T1558.003"),
 ("sql injection weakness", "CWE-89"), ("heap use after free", "CWE-416"), ("zip slip path traversal archive extraction", "CWE-22"),
 ("how to detect process hollowing", "Detection"), ("mitigations for T1059", "M10"),
]
def main():
    ok = ok1 = 0
    for q, exp in CASES:
        res = search(q, limit=3)
        h3 = any(exp.lower() in (r[0][5] + " " + r[0][1]).lower() for r in res)
        h1 = bool(res) and exp.lower() in (res[0][0][5] + " " + res[0][0][1]).lower()
        ok += h3; ok1 += h1
        print(f"{'PASS' if h3 else 'FAIL'} top1={'Y' if h1 else 'n'}  {q!r:46s} -> {res[0][0][1][:34]} · {res[0][0][2]}" if res else f"FAIL {q}")
    print(f"\ntop-3: {ok}/{len(CASES)}   top-1: {ok1}/{len(CASES)}")
main()
