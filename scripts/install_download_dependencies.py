"""Add the resolved, pinned downloader dependencies without replacing ISCE2's.

Conda-patched package metadata can already produce pip-check diagnostics.
Retain those diagnostics and reject any new ones instead of hiding them.
"""
import json
from importlib.metadata import distributions
from pathlib import Path
import subprocess
import sys


def installed():
    return {d.metadata["Name"].lower().replace("_", "-"): d.version for d in distributions()}


def check():
    result = subprocess.run([sys.executable, "-m", "pip", "check"], text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return set(result.stdout.splitlines()) if result.returncode else set()


before = installed()
issues_before = check()
requirements = Path(sys.argv[1])
for line in requirements.read_text().splitlines():
    if not line or line.startswith("#"):
        continue
    name, expected = line.split("==")
    existing = before.get(name.lower().replace("_", "-"))
    if existing is not None and existing != expected:
        raise RuntimeError(f"Refusing to replace existing package {name} {existing} with {expected}")

# Full dependency resolution was verified against the installed environment.
# All seven added distributions are pinned; pip check below verifies closure.
subprocess.run([
    sys.executable, "-X", "faulthandler", "-m", "pip", "install",
    "--disable-pip-version-check", "--no-cache-dir", "--no-deps",
    "--no-index", "--find-links", "/tmp/download-wheels",
    "--requirement", str(requirements),
], check=True)
after = installed()
changed = {name for name, version in before.items() if after.get(name) != version}
if changed:
    raise RuntimeError(f"Existing package versions changed: {sorted(changed)}")
issues_after = check()
if issues_after - issues_before:
    raise RuntimeError(f"New dependency conflicts: {sorted(issues_after - issues_before)}")
record_dir = Path("/opt/conda/share/isce2")
(record_dir / "python-before-download.txt").write_text(
    "\n".join(f"{name}=={version}" for name, version in sorted(before.items())) + "\n"
)
(record_dir / "download-dependencies.json").write_text(json.dumps({
    "added": {name: version for name, version in after.items() if name not in before},
    "existing_pip_check_diagnostics": sorted(issues_before),
    "new_pip_check_diagnostics": sorted(issues_after - issues_before),
}, indent=2) + "\n")
print("Downloader dependencies installed; existing packages unchanged; no new dependency conflicts.")
for issue in sorted(issues_before):
    print(f"Existing environment diagnostic: {issue}")
