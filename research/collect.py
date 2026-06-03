#!/usr/bin/env python3
"""Reproducible data-collection harness for the falsegreen validation campaign.

Runs the scanner over each project dir and appends one row per project to a CSV
(project, layer/code aggregates, high count). The seed for the empirical dataset
behind a paper on false-positive unit tests. Re-runnable; idempotent per project.

Usage:
  PYTHONPATH=<repo>/src python collect.py <projects_root> <out.csv>
"""
import csv, json, os, subprocess, sys
from collections import Counter

CODES = ["C1","C2","C2b","C3","C4","C4b","C5","C6","C7","C8","C9","C13","C13b",
         "C14","C16","C17","C18","C19","C20","C21","C22","CC"]

def count_tests(path):
    n = 0
    for r,_,fs in os.walk(path):
        for f in fs:
            if f.endswith(".py") and ("test" in f.lower()):
                try:
                    for line in open(os.path.join(r,f),encoding="utf-8",errors="replace"):
                        s=line.lstrip()
                        if s.startswith("def test") or s.startswith("async def test"):
                            n+=1
                except Exception: pass
    return n

def scan(path):
    out = subprocess.run([sys.executable,"-m","falsegreen","--format","json",path],
                         capture_output=True, text=True)
    try: return json.loads(out.stdout or "[]")
    except Exception: return []

def main():
    root, out_csv = sys.argv[1], sys.argv[2]
    rows=[]
    for name in sorted(os.listdir(root)):
        p=os.path.join(root,name)
        if not os.path.isdir(p): continue
        f=scan(p)
        bycode=Counter(r["code"] for r in f)
        bylayer=Counter(r.get("layer","logic") for r in f)
        row={"project":name,"tests":count_tests(p),"findings":len(f),
             "high":sum(1 for r in f if r["confidence"]=="high"),
             "web_browser":bylayer.get("web",0)+bylayer.get("browser",0)}
        for c in CODES: row[c]=bycode.get(c,0)
        rows.append(row); print(f"  {name}: {len(f)} findings, {row['high']} high")
    fields=["project","tests","findings","high","web_browser"]+CODES
    with open(out_csv,"w",newline="",encoding="utf-8") as fh:
        w=csv.DictWriter(fh,fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {out_csv}")

if __name__=="__main__": main()
