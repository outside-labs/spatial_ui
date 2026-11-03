"""Verify release archives and install each one outside the source checkout."""

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import tarfile
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]

INSTALLED_CHECK = """
from importlib.metadata import distribution
from importlib.resources import files
import spatial_ui
from spatial_ui import Session
from spatial_ui.demo import Dashboard, demo_stylesheet

package = distribution("spatial-ui")
assert package.version == spatial_ui.__version__
assert package.metadata["License-Expression"] == "MIT"
assert not package.requires
licenses = [path for path in package.files
            if str(path).endswith(".dist-info/licenses/LICENSE")]
assert len(licenses) == 1
assert "MIT License" in package.locate_file(licenses[0]).read_text(encoding="utf-8")
assert files("spatial_ui").joinpath("py.typed").is_file()
assert files("spatial_ui").joinpath("static", "runtime.js").is_file()
assert files("spatial_ui").joinpath("static", "live.js").is_file()
assert demo_stylesheet()
snapshot = Session(Dashboard()).render()
assert len(snapshot.event_ids) == 4
assert "spatial_ui" in snapshot.markup
"""

INSTALLED_LIVE = """
import hashlib, json, platform, sys, unittest
from importlib.resources import files
from pathlib import Path
import spatial_ui
assert Path(spatial_ui.__file__).is_relative_to(Path(sys.prefix))
suite = unittest.TestSuite(unittest.defaultTestLoader.discover(sys.argv[1], pattern=pattern)
                          for pattern in ("test_live.py", "test_asgi.py"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped and result.testsRun >= 16
assert not any(name in sys.modules for name in ("expose_api", "backpack_store", "enforce_type", "playwright"))
package = files("spatial_ui")
receipt = {"python": platform.python_version(), "os": platform.system(), "tests": result.testsRun,
           "skipped": len(result.skipped), "failures": len(result.failures), "errors": len(result.errors),
           "module_sha256": {name: hashlib.sha256(package.joinpath(name).read_bytes()).hexdigest()
                             for name in ("live.py", "resource_form.py", "static/live.js", "static/runtime.js")},
           "asset": str(package.joinpath("static", "live.js"))}
Path(sys.argv[2]).write_text(json.dumps(receipt), encoding="utf-8")
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require a release tag matching the package version")
    parser.add_argument("--receipt-file", type=Path)
    args = parser.parse_args()
    project = tomllib.loads(ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if args.tag is not None and args.tag != f"v{version}":
        parser.error(f"release tag must be v{version}, got {args.tag!r}")

    stem = f"spatial_ui-{version}"
    dist = ROOT / "dist"
    wheel = dist / f"{stem}-py3-none-any.whl"
    sdist = dist / f"{stem}.tar.gz"
    artifacts = {path for path in dist.iterdir() if not path.name.startswith(".")}
    assert artifacts == {wheel, sdist}, "expected exactly one wheel and one sdist"
    license_text = ROOT.joinpath("LICENSE").read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        prefix = f"{stem}.dist-info/"
        metadata = BytesParser().parsebytes(archive.read(prefix + "METADATA"))
        assert metadata["Name"] == project["name"] == "spatial-ui"
        assert metadata["Version"] == version
        assert metadata["Requires-Python"] == project["requires-python"]
        assert metadata["License-Expression"] == project["license"] == "MIT"
        assert metadata.get_all("License-File") == ["LICENSE"]
        assert not metadata.get_all("Requires-Dist"), "runtime must remain dependency-free"
        assert archive.read(prefix + "licenses/LICENSE") == license_text
        for name in ("py.typed", "live.py", "static/runtime.js", "static/live.js", "static/demo.css"):
            assert f"spatial_ui/{name}" in archive.namelist(), name
            assert archive.read(f"spatial_ui/{name}") == ROOT.joinpath("src", "spatial_ui", name).read_bytes(), name
    with tarfile.open(sdist) as archive:
        assert archive.extractfile(f"{stem}/LICENSE").read() == license_text
        assert archive.extractfile(f"{stem}/README.md").read() == ROOT.joinpath("README.md").read_bytes()

    receipts = []
    for artifact in (wheel, sdist):
        with tempfile.TemporaryDirectory(prefix="spatial-ui-install-") as temporary:
            envdir = Path(temporary) / "venv"
            subprocess.run(["uv", "venv", str(envdir), "--python", "3.14"], check=True)
            subprocess.run(["uv", "pip", "install", "--python", str(envdir), str(artifact)], check=True)
            bindir = envdir / ("Scripts" if os.name == "nt" else "bin")
            python = bindir / ("python.exe" if os.name == "nt" else "python")
            command = bindir / ("spatial-ui.exe" if os.name == "nt" else "spatial-ui")
            subprocess.run([str(python), "-I", "-c", INSTALLED_CHECK], cwd=temporary, check=True)
            test_directory = Path(temporary) / "tests"
            shutil.copytree(ROOT / "tests", test_directory, ignore=shutil.ignore_patterns("__pycache__"))
            receipt_path = Path(temporary) / "live.json"
            subprocess.run([str(python), "-I", "-W", "error::ResourceWarning", "-c", INSTALLED_LIVE,
                            str(test_directory), str(receipt_path)],
                           cwd=temporary, check=True)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            result = subprocess.run(["node", "--test-reporter=tap", str(test_directory / "live_runtime.mjs"), receipt.pop("asset")],
                                    cwd=temporary, check=True, capture_output=True, text=True)
            count = re.search(r"# tests (\d+)", result.stdout)
            assert count and int(count[1]) >= 9 and "# fail 0" in result.stdout and "# skipped 0" in result.stdout, result.stdout
            receipt.update(artifact=artifact.name, artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                           protocol_tests=int(count[1]), base_isolation=True)
            receipts.append(receipt)
            for argv in ([str(python), "-I", "-m", "spatial_ui", "--version"],
                         [str(command), "--version"]):
                result = subprocess.run(argv, cwd=temporary, check=True, capture_output=True, text=True)
                assert result.stdout.strip() == f"spatial-ui {version}", result.stdout
            print(f"Verified {artifact.name}", flush=True)
    if args.receipt_file:
        args.receipt_file.write_text(json.dumps({"profile": "spatial-live/1", "installations": receipts}, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
