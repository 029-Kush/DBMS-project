"""Exact-lookup checks. Each: (query, expected substring that must appear in the top-3 results, verbatim).
Expectations were written from the tools' own documented behaviour, then checked against the stored man/help text."""
import sys
from search import search

CASES = [
 ("nmap -sU", "UDP scans"), ("nmap -sS", "TCP SYN"), ("nmap -Pn", "Treat all hosts as online"),
 ("nmap --script", "script scan"), ("nmap -sV", "version"), ("nmap -oN", "normal"),
 ("hashcat -m 1000", "hash-type"), ("hashcat -a 3", "attack-mode"),
 ("tcpdump -nn", "convert protocol and port numbers"), ("tcpdump -i", "interface"),
 ("curl -k", "insecure"), ("curl -X", "request"), ("curl -L", "location"),
 ("ssh -L", "forward"), ("ssh -i", "identity"),
 ("strace -f", "fork"), ("strace -p", "pid"), ("objdump -d", "disassemble"),
 ("readelf -h", "header"), ("iptables -A", "append"),
 ("hydra -l", "login"), ("ffuf -w", "wordlist"), ("dig +short", "short"),
 ("openssl s_client", "client"), ("gdb -p", "process"),
 ("scan UDP ports with nmap", "UDP"), ("capture traffic on one interface tcpdump", "interface"),
 ("follow child processes strace", "fork"), ("ignore TLS certificate errors curl", "insecure"),
]

def main():
    ok = 0
    for q, exp in CASES:
        res = search(q, limit=3)
        hit1 = bool(res) and exp.lower() in res[0][0][5].lower()
        hit3 = any(exp.lower() in r[0][5].lower() for r in res)
        ok += hit3
        print(f"{'PASS' if hit3 else 'FAIL'} top1={'Y' if hit1 else 'n'}  {q!r:50s} -> {res[0][0][1]+' '+res[0][0][2] if res else 'none'}")
    print(f"\n{ok}/{len(CASES)} in top-3")

if __name__ == "__main__":
    main()
