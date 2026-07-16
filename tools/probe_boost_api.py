"""Probe whether TRUMPF TruTops Boost exposes a PROGRAMMABLE interface.

WHY THIS MATTERS
----------------
AutoBoost drives Boost by GUI automation over RDP: every click and screenshot
pays a round-trip, and the cut phase waits on real CAM computation. Profiling
put the steady-state part at ~53s, and the honest floor for this approach is
~46-48s. The only way under that -- toward the "sub-30s/part" goal -- is to stop
driving pixels and call Boost directly, IF Boost exposes a scripting surface.

Many CAD/CAM packages (older TruTops included) ship an OLE/COM Automation
interface, a type library describing it, or a .NET API. If Boost has one, we
could open/place/save/cut a part with method calls instead of mouse moves --
collapsing the whole UIA-and-clicks layer and the RDP latency with it. If it has
none, we know GUI automation is the ceiling and can stop looking.

This script answers that question. It does NOT change anything in Boost, does NOT
touch the AutoBoost app, and by default does NOT even instantiate anything -- it
only READS the Windows registry, the Boost install folder, the Running Object
Table, and any type library it finds.

WHAT IT LOOKS FOR
-----------------
  1. COM/OLE Automation      registry ProgIDs + CLSIDs whose class name or server
                             .exe/.dll path names TRUMPF/TruTops/Boost, with their
                             LocalServer32/InprocServer32 path and TypeLib link.
  2. Type libraries          registered .tlb/.olb (and any in the install dir):
                             dumped to show the actual scriptable methods
                             (Open, Save, CreateProgram, ... = a usable API).
  3. .NET assemblies         DLLs/EXEs in the install dir flagged as managed
                             (CLR header present) with API-suggestive names.
  4. Running Object Table    live automation objects an already-open Boost has
                             published, that a script could attach to.
  5. The Boost process       its .exe path (locates the install dir even if COM
                             is absent), via the open Boost window.

Usage (on the workstation, Boost may be open or closed):

    py tools\\probe_boost_api.py                  # full passive probe (default)
    py tools\\probe_boost_api.py --fast           # skip the full CLSID sweep
    py tools\\probe_boost_api.py --out report.txt # choose the report path
    py tools\\probe_boost_api.py --keywords tops trulaser   # widen the match

    # OPT-IN, may start/attach Boost -- only after the passive probe finds a ProgID:
    py tools\\probe_boost_api.py --instantiate TruTops.Application

It prints a report and writes it to boost_api_probe_<stamp>.txt (or --out).
Send that file back.

WHAT THE RESULT MEANS
---------------------
  - A ProgID + a type library full of methods  -> a real API exists; sub-30s is
    on the table and worth a spike against it.
  - A ProgID but no readable type library      -> an API likely exists but is
    late-bound/undocumented; needs a deeper look.
  - Managed API-named assemblies only           -> a .NET API may exist; would be
    called via pythonnet/clr rather than COM.
  - Nothing across all five                     -> GUI automation is the ceiling;
    stay on AutoBoost and pursue stencil-only for the jobs that allow it.

Read-only. Non-destructive. Independent of the AutoBoost package.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time

DEFAULT_KEYWORDS = ["trumpf", "trutops", "boost"]


# --------------------------------------------------------------------------- #
# Pure helpers (no Windows dependency -- unit-testable anywhere via --selftest) #
# --------------------------------------------------------------------------- #

def make_matcher(keywords):
    """Return match(text)->bool: True if any keyword appears (case-insensitive)."""
    kws = [k.lower() for k in keywords if k]

    def match(text) -> bool:
        if not text:
            return False
        low = str(text).lower()
        return any(k in low for k in kws)

    return match


def exe_from_server(server: str) -> str:
    """Extract the module path from a LocalServer32/InprocServer32 value, which
    may be quoted and carry arguments, e.g. '"C:\\..\\Boost.exe" /automation'."""
    if not server:
        return ""
    s = server.strip()
    if s.startswith('"'):
        end = s.find('"', 1)
        return s[1:end] if end > 0 else s[1:]
    # Unquoted: take up to the first .exe/.dll token boundary, else the whole thing.
    low = s.lower()
    for ext in (".exe", ".dll", ".ocx"):
        i = low.find(ext)
        if i > 0:
            return s[: i + len(ext)]
    return s.split(" ")[0]


def is_clr_assembly(path: str) -> bool:
    """True if `path` is a managed (.NET) PE image: parse the PE optional header
    and check data directory 14 (the CLR runtime header) is present.

    Pure stdlib; the offsets of NumberOfRvaAndSizes are fixed by the PE spec
    (PE32 -> data dirs at 96, PE32+ -> 112; each dir is 8 bytes; CLR = index 14).
    """
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return False
            f.seek(0x3C)
            e_lfanew = struct.unpack("<I", f.read(4))[0]
            f.seek(e_lfanew)
            if f.read(4) != b"PE\0\0":
                return False
            coff = f.read(20)
            if len(coff) < 20:
                return False
            opt_size = struct.unpack("<H", coff[16:18])[0]
            opt = f.read(opt_size)
            if len(opt) < 2:
                return False
            magic = struct.unpack("<H", opt[:2])[0]
            if magic == 0x10B:        # PE32
                dd_start = 96
            elif magic == 0x20B:      # PE32+
                dd_start = 112
            else:
                return False
            off = dd_start + 14 * 8
            if off + 8 > len(opt):
                return False
            rva, size = struct.unpack("<II", opt[off:off + 8])
            return size > 0
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Registry sweep (Windows)                                                     #
# --------------------------------------------------------------------------- #

def _iter_subkeys(root, path):
    import winreg
    try:
        k = winreg.OpenKey(root, path)
    except OSError:
        return
    try:
        i = 0
        while True:
            try:
                yield winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
    finally:
        winreg.CloseKey(k)


def _read_default(root, path):
    import winreg
    try:
        with winreg.OpenKey(root, path) as k:
            val, _ = winreg.QueryValueEx(k, "")
            return val
    except OSError:
        return None


def scan_clsids(match, fast: bool):
    """Sweep HKCR\\CLSID (and Wow6432Node) for classes whose name/server/ProgID
    matches. Returns (hits, install_dirs). Passive: only reads keys."""
    import os
    import winreg
    hits, install_dirs = [], set()
    bases = ["CLSID", r"Wow6432Node\CLSID"]
    if fast:
        # Fast mode still scans CLSID but bails early on the huge default view is
        # not really possible without enumerating; --fast instead just limits the
        # 64-bit view (skip Wow6432Node) to roughly halve the work.
        bases = ["CLSID"]
    for base in bases:
        for clsid in _iter_subkeys(winreg.HKEY_CLASSES_ROOT, base):
            kp = f"{base}\\{clsid}"
            name = _read_default(winreg.HKEY_CLASSES_ROOT, kp)
            inproc = _read_default(winreg.HKEY_CLASSES_ROOT, kp + r"\InprocServer32")
            local = _read_default(winreg.HKEY_CLASSES_ROOT, kp + r"\LocalServer32")
            progid = _read_default(winreg.HKEY_CLASSES_ROOT, kp + r"\ProgID")
            viprog = _read_default(winreg.HKEY_CLASSES_ROOT, kp + r"\VersionIndependentProgID")
            typelib = _read_default(winreg.HKEY_CLASSES_ROOT, kp + r"\TypeLib")
            server = local or inproc or ""
            blob = " ".join(x for x in (name, server, progid, viprog) if x)
            if not match(blob):
                continue
            exe = exe_from_server(server)
            if exe:
                d = os.path.dirname(exe)
                if d:
                    install_dirs.add(d)
            hits.append({
                "clsid": clsid, "name": name, "progid": progid or viprog,
                "server": server, "server_kind": "Local" if local else ("Inproc" if inproc else "?"),
                "typelib": typelib, "managed": bool(exe and is_clr_assembly(exe)),
                "base": base,
            })
    return hits, install_dirs


def scan_typelibs(match):
    """Sweep HKCR\\TypeLib for registered type libraries whose name/path matches.
    Returns a list of {name, version, guid, path}."""
    import winreg
    out = []
    for base in ("TypeLib", r"Wow6432Node\TypeLib"):
        for guid in _iter_subkeys(winreg.HKEY_CLASSES_ROOT, base):
            gp = f"{base}\\{guid}"
            for ver in _iter_subkeys(winreg.HKEY_CLASSES_ROOT, gp):
                vp = f"{gp}\\{ver}"
                name = _read_default(winreg.HKEY_CLASSES_ROOT, vp)
                path = None
                # The .tlb path hangs under <lcid>\win32|win64|win64arm.
                for lcid in _iter_subkeys(winreg.HKEY_CLASSES_ROOT, vp):
                    for arch in ("win64", "win32", "win64arm"):
                        p = _read_default(winreg.HKEY_CLASSES_ROOT, f"{vp}\\{lcid}\\{arch}")
                        if p:
                            path = p
                            break
                    if path:
                        break
                if match(name) or match(path):
                    out.append({"guid": guid, "version": ver, "name": name, "path": path})
    return out


# --------------------------------------------------------------------------- #
# Filesystem sweep                                                            #
# --------------------------------------------------------------------------- #

API_HINTS = ("api", "automation", "interop", "sdk", "script", "addin",
             "plugin", "macro", "remote", "com", "oleaut")


def guess_install_dirs(match):
    """Common install roots plus any Program Files subdir whose name matches."""
    import glob
    import os
    dirs = set()
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
        r"C:\TRUMPF", r"C:\Trumpf",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for sub in glob.glob(os.path.join(root, "*")):
            if os.path.isdir(sub) and match(os.path.basename(sub)):
                dirs.add(sub)
    return dirs


def scan_install_files(dirs, match, max_files=40000):
    """Walk the install dirs. Return (tlbs, managed_hits, doc_hits). Bounded."""
    import os
    tlbs, managed, docs = [], [], []
    seen = 0
    for d in sorted(dirs):
        if not d or not os.path.isdir(d):
            continue
        for root, _sub, files in os.walk(d):
            for fn in files:
                seen += 1
                if seen > max_files:
                    return tlbs, managed, docs, True
                low = fn.lower()
                full = os.path.join(root, fn)
                if low.endswith((".tlb", ".olb")):
                    tlbs.append(full)
                elif low.endswith((".dll", ".exe")):
                    hinted = any(h in low for h in API_HINTS)
                    if hinted or match(low):
                        if is_clr_assembly(full):
                            managed.append((full, "managed"))
                        else:
                            managed.append((full, "native"))
                elif low.endswith((".chm", ".pdf")) and (
                        any(h in low for h in API_HINTS) or match(low)):
                    docs.append(full)
    return tlbs, managed, docs, False


# --------------------------------------------------------------------------- #
# Type-library introspection (comtypes -- already an AutoBoost dependency)     #
# --------------------------------------------------------------------------- #

_TKIND = {0: "enum", 1: "record", 2: "module", 3: "interface",
          4: "dispatch", 5: "coclass", 6: "alias", 7: "union"}


def dump_typelib(path, max_methods=80):
    """Best-effort dump of a type library's coclasses/interfaces and their
    method names -- the actual scriptable API surface. Read-only."""
    lines = []
    try:
        from comtypes.typeinfo import LoadTypeLib
    except Exception as exc:
        return [f"    (comtypes not available to read type libraries: {exc!r})"]
    try:
        tlib = LoadTypeLib(path)
    except Exception as exc:
        return [f"    (could not load type library: {exc!r})"]
    try:
        count = tlib.GetTypeInfoCount()
    except Exception as exc:
        return [f"    (type library opened but unreadable: {exc!r})"]
    for i in range(count):
        try:
            ti = tlib.GetTypeInfo(i)
            ta = ti.GetTypeAttr()
            kind = _TKIND.get(getattr(ta, "typekind", -1), f"kind{getattr(ta,'typekind','?')}")
            name = ti.GetDocumentation(-1)[0]
        except Exception:
            continue
        header = f"    [{kind}] {name}"
        methods = []
        try:
            for j in range(getattr(ta, "cFuncs", 0)):
                try:
                    fd = ti.GetFuncDesc(j)
                    mname = ti.GetDocumentation(fd.memid)[0]
                    if mname:
                        methods.append(mname)
                except Exception:
                    continue
        except Exception:
            pass
        if methods:
            shown = methods[:max_methods]
            more = f"  (+{len(methods) - len(shown)} more)" if len(methods) > len(shown) else ""
            header += f"  -- {len(methods)} method(s): " + ", ".join(shown) + more
        lines.append(header)
    return lines or ["    (type library contained no readable type info)"]


# --------------------------------------------------------------------------- #
# Running Object Table + process path (best-effort)                           #
# --------------------------------------------------------------------------- #

def enum_rot(match):
    """List Running Object Table monikers matching the keywords. Prefers
    pythoncom (pywin32); returns (hits, note). Read-only."""
    try:
        import pythoncom
    except Exception:
        return None, ("pythoncom (pywin32) not installed -- ROT skipped. "
                      "It is optional; install pywin32 to enable it.")
    hits = []
    try:
        rot = pythoncom.GetRunningObjectTable()
        ctx = pythoncom.CreateBindCtx(0)
        for moniker in rot:
            try:
                name = moniker.GetDisplayName(ctx, None)
            except Exception:
                continue
            if match(name):
                hits.append(name)
    except Exception as exc:
        return None, f"ROT enumeration failed: {exc!r}"
    return hits, None


def boost_process_path():
    """Path of the running Boost .exe via its open window, or None. Uses
    pywinauto (already an AutoBoost dependency) + ctypes; read-only."""
    try:
        from pywinauto import Desktop
    except Exception as exc:
        return None, f"pywinauto not available: {exc!r}"
    pid = None
    try:
        d = Desktop(backend="uia")
        for title_re in (r".* - TruTops Boost - .*", r"TruTops Boost.*"):
            try:
                w = d.window(title_re=title_re, control_type="Window")
                if w.exists(timeout=1):
                    pid = w.process_id()
                    break
            except Exception:
                continue
    except Exception as exc:
        return None, f"could not find a Boost window: {exc!r}"
    if not pid:
        return None, "no open Boost window found (start Boost to locate its .exe)"
    try:
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None, f"OpenProcess failed for pid {pid}"
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                h, 0, buf, ctypes.byref(size))
            return (buf.value if ok else None,
                    None if ok else f"QueryFullProcessImageName failed for pid {pid}")
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception as exc:
        return None, f"process path lookup failed: {exc!r}"


def try_instantiate(progid):
    """OPT-IN: CreateObject(progid) and list its dispatch methods, then release.
    May start or attach Boost. Returns report lines."""
    lines = [f"Instantiating {progid!r} (this may start/attach Boost)..."]
    try:
        import comtypes.client
    except Exception as exc:
        return lines + [f"  comtypes not available: {exc!r}"]
    try:
        obj = comtypes.client.CreateObject(progid)
    except Exception as exc:
        return lines + [f"  CreateObject failed: {exc!r}"]
    lines.append(f"  CreateObject OK -> {type(obj).__name__}")
    try:
        names = sorted({n for n in dir(obj) if not n.startswith("_")})
        lines.append(f"  {len(names)} public member(s): " + ", ".join(names[:120]))
    except Exception as exc:
        lines.append(f"  could not enumerate members: {exc!r}")
    try:
        del obj  # release; do not call any method
    except Exception:
        pass
    return lines


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #

def run_probe(keywords, fast, out_path, instantiate):
    import os
    match = make_matcher(keywords)
    R = []
    def add(s=""):
        R.append(s)

    add("=" * 72)
    add("AutoBoost -- Boost programmable-interface probe")
    add(f"when: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    add(f"keywords: {', '.join(keywords)}")
    add(f"platform: {sys.platform}")
    add("=" * 72)

    if not sys.platform.startswith("win"):
        add("")
        add("NOT running on Windows -- the registry/COM/process checks need the")
        add("workstation. Run this on the machine where Boost is installed.")
        _emit(R, out_path)
        return

    # 1. The Boost process / install dir
    add("")
    add("[1] Running Boost process")
    ppath, pnote = boost_process_path()
    install_dirs = set()
    if ppath:
        add(f"    exe: {ppath}")
        install_dirs.add(os.path.dirname(ppath))
    else:
        add(f"    {pnote}")

    # 2. COM CLSID sweep
    add("")
    add(f"[2] COM classes matching {keywords} (registry sweep{' -- fast' if fast else ''})")
    try:
        hits, dirs = scan_clsids(match, fast)
        install_dirs |= dirs
        if hits:
            for h in hits:
                mflag = " [.NET]" if h["managed"] else ""
                add(f"    - {h.get('progid') or '(no ProgID)'}{mflag}")
                add(f"        name   : {h.get('name')}")
                add(f"        server : [{h['server_kind']}] {h.get('server')}")
                add(f"        clsid  : {h['clsid']}  typelib: {h.get('typelib')}")
        else:
            add("    none found -- no COM class names/servers matched.")
    except Exception as exc:
        add(f"    sweep error: {exc!r}")

    # 3. Registered type libraries
    add("")
    add("[3] Registered type libraries matching")
    tlb_paths = []
    try:
        tls = scan_typelibs(match)
        if tls:
            for t in tls:
                add(f"    - {t['name']}  (v{t['version']})")
                add(f"        path: {t['path']}")
                if t["path"]:
                    tlb_paths.append(t["path"])
        else:
            add("    none registered under matching names/paths.")
    except Exception as exc:
        add(f"    sweep error: {exc!r}")

    # 4. Install-dir file scan
    add("")
    add("[4] Install-directory scan")
    install_dirs |= guess_install_dirs(match)
    if install_dirs:
        add("    dirs: " + "; ".join(sorted(install_dirs)))
        try:
            tlbs, mods, docs, truncated = scan_install_files(install_dirs, match)
            for p in tlbs:
                if p not in tlb_paths:
                    tlb_paths.append(p)
            add(f"    type libraries (.tlb/.olb): {len(tlbs)}")
            for p in tlbs[:40]:
                add(f"        {p}")
            managed = [p for p, k in mods if k == "managed"]
            native = [p for p, k in mods if k == "native"]
            add(f"    API-named MANAGED (.NET) modules: {len(managed)}")
            for p in managed[:40]:
                add(f"        {p}")
            add(f"    API-named native modules: {len(native)}")
            for p in native[:20]:
                add(f"        {p}")
            if docs:
                add(f"    API-named docs: {len(docs)}")
                for p in docs[:20]:
                    add(f"        {p}")
            if truncated:
                add("    (file scan hit its cap -- some dirs not fully walked)")
        except Exception as exc:
            add(f"    scan error: {exc!r}")
    else:
        add("    no matching install directory located.")

    # 5. Type-library method dump (the payload)
    add("")
    add("[5] Type-library contents (scriptable methods)")
    if tlb_paths:
        for p in tlb_paths:
            add(f"    == {p} ==")
            for line in dump_typelib(p):
                add(line)
    else:
        add("    no type library to inspect.")

    # 6. Running Object Table
    add("")
    add("[6] Running Object Table (live attachable objects)")
    rot, note = enum_rot(match)
    if rot is None:
        add(f"    {note}")
    elif rot:
        for name in rot:
            add(f"    - {name}")
    else:
        add("    no matching object currently published (is Boost open?).")

    # 7. Optional instantiation
    if instantiate:
        add("")
        add("[7] Instantiation (opt-in)")
        for line in try_instantiate(instantiate):
            add(f"    {line}")

    # Verdict
    add("")
    add("-" * 72)
    add("READ THIS")
    add("  * ProgID + a type library listing methods (Open/Save/Program/...) ->")
    add("    a real scripting API exists; a direct-call spike is worth doing.")
    add("  * ProgID but no readable type library -> likely late-bound API; dig")
    add("    deeper (try --instantiate <ProgID> and inspect its members).")
    add("  * Only API-named .NET assemblies -> a managed API; call via pythonnet.")
    add("  * Nothing anywhere -> GUI automation is the ceiling; stay on AutoBoost.")
    add("-" * 72)

    _emit(R, out_path)


def _emit(lines, out_path):
    text = "\n".join(lines)
    print(text)
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\n[report written to {out_path}]")
    except OSError as exc:
        print(f"\n[could not write report: {exc!r}]")


def _selftest() -> int:
    """Exercise the pure helpers without Windows."""
    m = make_matcher(DEFAULT_KEYWORDS)
    assert m("C:\\Program Files\\TRUMPF\\TruTops Boost\\Boost.exe")
    assert m("TruTops.Application")
    assert not m("C:\\Windows\\System32\\notepad.exe")
    assert not m("")
    assert exe_from_server('"C:\\a b\\Boost.exe" /automation') == "C:\\a b\\Boost.exe"
    assert exe_from_server("C:\\x\\srv.dll") == "C:\\x\\srv.dll"
    assert exe_from_server("") == ""
    assert is_clr_assembly(__file__) is False           # this .py is not a PE
    assert is_clr_assembly("/no/such/file") is False
    print("selftest OK")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(
        description="Passively probe TruTops Boost for a programmable interface.")
    ap.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS,
                    help="Match tokens for class/file/library names.")
    ap.add_argument("--fast", action="store_true",
                    help="Skip the 32-bit (Wow6432Node) CLSID view to halve the sweep.")
    ap.add_argument("--out", default=None, help="Report file path.")
    ap.add_argument("--instantiate", default=None, metavar="PROGID",
                    help="OPT-IN: CreateObject(PROGID) and list its members. "
                         "May start/attach Boost. Use only after the passive probe.")
    ap.add_argument("--selftest", action="store_true",
                    help="Run the pure-helper self-test and exit (no Windows needed).")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    out = args.out or time.strftime("boost_api_probe_%Y%m%d_%H%M%S.txt")
    run_probe(args.keywords, args.fast, out, args.instantiate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
