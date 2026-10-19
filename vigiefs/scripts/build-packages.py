"""Fabrique les paquets Windows prêts à l'emploi (Python embarqué, aucune installation préalable).

    python scripts/build-packages.py [--python 3.13.16] [--out dist]

Produit dans --out :
    VigieFS-<version>-WindowsServer.zip   service 24 h/24 : INSTALLER-VigieFS.bat, WinSW inclus
    VigieFS-<version>-PC-Windows.zip      poste : demo-vigiefs.bat, lancer-vigiefs.bat

Sources (téléchargées une fois dans --out/cache) :
    - Python : paquet NuGet officiel « python » publié par la Python Software Foundation (dossier tools/) ;
    - bibliothèques : roues Windows 64 bits depuis PyPI (pip install --target --platform win_amd64) ;
    - WinSW v2.12.0 (MIT), variante .NET Framework 4.6.1, depuis les versions publiées sur GitHub.
Les empreintes SHA-256 des éléments téléchargés sont consignées dans runtime/VERSIONS.txt.
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NUGET = "https://api.nuget.org/v3-flatcontainer/python/{v}/python.{v}.nupkg"
WINSW = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe"
WINSW_LICENSE = """MIT License

Copyright (c) 2008-2020 Kohsuke Kawaguchi, Sun Microsystems, Inc., CloudBees, Inc.,
Oleg Nenashev and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Source : https://github.com/winsw/winsw (WinSW v2.12.0, renommé VigieFS-Service.exe)
"""
# Fichiers et dossiers du projet jamais livrés.
EXCLUDE_DIRS = {".git", ".venv", "data", "data-demo", "dist", "tools", "tests", "packaging", "__pycache__",
                ".pytest_cache", "runtime"}
EXCLUDE_FILES = {".gitignore", "requirements-dev.txt", "build-packages.py"}
# Lanceurs livrés à la racine de chaque paquet.
LAUNCHERS = {"server": {"INSTALLER-VigieFS.bat", "DESINSTALLER-VigieFS.bat"},
             "pc": {"demo-vigiefs.bat", "lancer-vigiefs.bat", "arreter-vigiefs.bat"}}


def version():
    src = open(os.path.join(ROOT, "vigiefs", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__ = "([^"]+)"', src).group(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, dest):
    if not os.path.isfile(dest):
        print(f"  téléchargement {url}")
        tmp = dest + ".part"
        with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as out:  # noqa: S310
            shutil.copyfileobj(resp, out)
        os.replace(tmp, dest)
    return dest


def build_runtime(work, cache, py_version):
    """Python officiel (NuGet) + bibliothèques Windows 64 bits dans runtime/Lib/site-packages."""
    runtime = os.path.join(work, "runtime")
    if os.path.isdir(runtime):
        return runtime
    nupkg = download(NUGET.format(v=py_version), os.path.join(cache, f"python.{py_version}.nupkg"))
    with zipfile.ZipFile(nupkg) as zf:
        for item in zf.infolist():
            if item.filename.startswith("tools/") and not item.is_dir():
                target = os.path.join(runtime, *item.filename[len("tools/"):].split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(item) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    site = os.path.join(runtime, "Lib", "site-packages")
    major_minor = "".join(py_version.split(".")[:2])
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--target", site, "--platform", "win_amd64", "--python-version", py_version.rsplit(".", 1)[0],
                    "--implementation", "cp", "--abi", f"cp{major_minor}", "--only-binary=:all:",
                    "-r", os.path.join(ROOT, "requirements.txt"),
                    "colorama"],  # dépendance Windows de click, non résolue par pip hors de Windows
                   check=True)
    for extra in ("bin",):
        shutil.rmtree(os.path.join(site, extra), ignore_errors=True)
    for dirpath, dirnames, _ in os.walk(runtime):
        for d in [d for d in dirnames if d == "__pycache__"]:
            shutil.rmtree(os.path.join(dirpath, d))
            dirnames.remove(d)
    # « python -m vigiefs » fonctionne depuis n'importe quel dossier (services, invite de commandes).
    with open(os.path.join(site, "vigiefs-app.pth"), "w", encoding="ascii") as fh:
        fh.write("..\\..\\..\n")
    packages = sorted(" ".join(d[:-len(".dist-info")].rsplit("-", 1))
                      for d in os.listdir(site) if d.endswith(".dist-info"))
    with open(os.path.join(runtime, "VERSIONS.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"Python {py_version} (NuGet python.{py_version}.nupkg, SHA-256 {sha256(nupkg)})\n\n")
        fh.write("Bibliothèques (roues Windows 64 bits, PyPI) :\n")
        fh.writelines(f"  {p}\n" for p in packages)
    return runtime


def project_files(kind):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or name.endswith((".pyc", ".zip")):
                continue
            if dirpath == ROOT and name.endswith((".bat", ".sh")) and name not in LAUNCHERS[kind]:
                continue
            yield os.path.relpath(os.path.join(dirpath, name), ROOT)


def windows_text(text):
    """UTF-8 avec BOM et fins de ligne CRLF : lisible tel quel dans le Bloc-notes."""
    return "﻿" + text.replace("\r\n", "\n").replace("\n", "\r\n")


def make_zip(kind, out_dir, runtime, winsw, py_version):
    ver = version()
    label = {"server": "WindowsServer", "pc": "PC-Windows"}[kind]
    top = f"VigieFS-{ver}-{label}"
    path = os.path.join(out_dir, f"{top}.zip")
    readme = open(os.path.join(ROOT, "packaging", f"LISEZ-MOI-{'serveur' if kind == 'server' else 'PC'}.txt"),
                  encoding="utf-8").read().replace("{version}", ver).replace("{python}", py_version)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{top}/LISEZ-MOI.txt", windows_text(readme).encode("utf-8"))
        for rel in project_files(kind):
            zf.write(os.path.join(ROOT, rel), f"{top}/{rel.replace(os.sep, '/')}")
        for dirpath, _, filenames in os.walk(runtime):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                zf.write(full, f"{top}/runtime/{os.path.relpath(full, runtime).replace(os.sep, '/')}")
        if kind == "server":
            zf.write(winsw, f"{top}/tools/VigieFS-Service.exe")
            zf.writestr(f"{top}/tools/WinSW-LICENSE.txt",
                        windows_text(WINSW_LICENSE + f"\nSHA-256 : {sha256(winsw)}\n").encode("utf-8"))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default="3.13.16", help="version du Python embarqué (paquet NuGet)")
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = parser.parse_args()
    out = os.path.abspath(args.out)
    cache = os.path.join(out, "cache")
    os.makedirs(cache, exist_ok=True)
    print("Préparation du Python embarqué…")
    runtime = build_runtime(os.path.join(cache, f"work-{args.python}"), cache, args.python)
    winsw = download(WINSW, os.path.join(cache, "WinSW.NET461.exe"))
    for kind in ("server", "pc"):
        path = make_zip(kind, out, runtime, winsw, args.python)
        print(f"  {os.path.basename(path)} : {os.path.getsize(path) / 1048576:.1f} Mo")


if __name__ == "__main__":
    main()
