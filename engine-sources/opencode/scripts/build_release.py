#!/usr/bin/env python3
"""Build deterministic source, macOS/Linux, and Windows archives."""

from __future__ import annotations

import argparse, hashlib, json, os, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; NAME="opencode-bounded-orchestrator"; STAMP=(1980,1,1,0,0,0)
SKIP={".git","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",".DS_Store","Thumbs.db","dist"}


def excluded(relative: Path) -> bool:
    if any(part in SKIP for part in relative.parts) or relative.suffix.lower() in {".pyc",".pyo",".zip",".bundle"}: return True
    value=relative.as_posix()
    if value.startswith(".opencode/.bounded-orchestrator/") and value != ".opencode/.bounded-orchestrator/.gitignore": return True
    if value.startswith(".opencode/.candidate/") and value != ".opencode/.candidate/.gitignore": return True
    if relative.name==".env" or relative.name.startswith(".env."): return True
    return False


def files() -> list[Path]: return sorted((p.relative_to(ROOT) for p in ROOT.rglob("*") if p.is_file() and not p.is_symlink() and not excluded(p.relative_to(ROOT))),key=lambda p:p.as_posix())


def info(name: str, executable: bool=False) -> zipfile.ZipInfo:
    item=zipfile.ZipInfo(name,STAMP); item.compress_type=zipfile.ZIP_DEFLATED; item.create_system=3; item.external_attr=((0o755 if executable else 0o644)&0xffff)<<16; return item


def archive(path: Path, selected: list[Path], platform: str, start: tuple[str,str]|None=None) -> None:
    executable={"setup.command","scripts/install.sh","scripts/install.py","scripts/dashboard.py","launchers/launch_dashboard.py","launchers/Ustam.app/Contents/MacOS/launch","scripts/validate.py","scripts/smoke_opencode.py","scripts/build_release.py",".opencode/tools/candidate.py",".opencode/tools/ledger.py",".opencode/tools/usage_report.py",".opencode/tools/local_eval.py"}
    prefix=NAME+"/"
    with zipfile.ZipFile(path,"w") as z:
        for relative in selected:
            data=(ROOT/relative).read_bytes()
            if platform=="windows" and relative.suffix.lower() in {".cmd",".ps1"}: data=data.replace(b"\r\n",b"\n").replace(b"\n",b"\r\n")
            z.writestr(info(prefix+relative.as_posix(),relative.as_posix() in executable),data)
        if start: z.writestr(info(prefix+start[0]),start[1].encode())


def sha(path: Path) -> str:
    h=hashlib.sha256(); h.update(path.read_bytes()); return h.hexdigest()


def build(destination: Path) -> dict:
    version=(ROOT/"VERSION").read_text().strip(); destination.mkdir(parents=True,exist_ok=True); selected=files()
    paths={
      "source":destination/f"{NAME}-v{version}-source.zip",
      "macos-linux":destination/f"{NAME}-v{version}-macos-linux.zip",
      "windows":destination/f"{NAME}-v{version}-windows.zip"}
    archive(paths["source"],selected,"source")
    archive(paths["macos-linux"],selected,"posix",("START-HERE-MACOS-LINUX.txt",f"Ustam {version}\nExtract fully. On macOS open launchers/Ustam.app. If asked, the first folder picker opens in Downloads: choose the OUTER folder extracted from the opencode-bounded-orchestrator ZIP (with launchers and scripts). Then choose the separate Git project where you use OpenCode. Review and install in the browser. Python 3.11+ is required. The setup.command installer remains available; on Linux run python3 scripts/dashboard.py PROJECT.\n"))
    archive(paths["windows"],selected,"windows",("START-HERE-WINDOWS.txt",f"Ustam {version}\r\nExtract fully, open launchers/Launch Ustam.vbs, choose a project folder, and use the browser to review and install. Python 3.11+ is required. The setup.cmd installer remains available.\r\n"))
    sums="".join(f"{sha(path)}  {path.name}\n" for path in paths.values()); (destination/"SHA256SUMS").write_text(sums)
    return {"version":version,"artifacts":{key:{"path":str(path),"sha256":sha(path)} for key,path in paths.items()},"checksums":str(destination/"SHA256SUMS")}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,default=ROOT/"dist"); args=parser.parse_args(); print(json.dumps(build(args.output.resolve()),indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
