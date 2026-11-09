"""Prints HTTPS_PROXY from ~/.claude/settings.json even when that file is not valid JSON.
fl_triage.cmd sets it for claude -p, so a broken settings.json does not cut triage off from the API (08.11)."""
import os, re
try:
    s = open(os.path.expanduser(r"~\.claude\settings.json"), encoding="utf-8-sig").read()
    m = re.search(r'"HTTPS_PROXY"\s*:\s*"([^"]+)"', s)
    if m:
        print(m.group(1))
except OSError:
    pass
