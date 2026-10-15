"""Verify release archives and install each one outside the source checkout."""

import argparse
from email.parser import BytesParser
import os
from pathlib import Path
import subprocess
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
assert demo_stylesheet()
snapshot = Session(Dashboard()).render()
assert len(snapshot.event_ids) == 4
assert "spatial_ui" in snapshot.markup
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require a release tag matching the package version")
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
        for name in ("py.typed", "static/runtime.js", "static/demo.css"):
            assert f"spatial_ui/{name}" in archive.namelist(), name
    with tarfile.open(sdist) as archive:
        assert archive.extractfile(f"{stem}/LICENSE").read() == license_text
        assert archive.extractfile(f"{stem}/README.md").read() == ROOT.joinpath("README.md").read_bytes()

    for artifact in (wheel, sdist):
        with tempfile.TemporaryDirectory(prefix="spatial-ui-install-") as temporary:
            envdir = Path(temporary) / "venv"
            subprocess.run(["uv", "venv", str(envdir), "--python", "3.14"], check=True)
            subprocess.run(["uv", "pip", "install", "--python", str(envdir), str(artifact)], check=True)
            bindir = envdir / ("Scripts" if os.name == "nt" else "bin")
            python = bindir / ("python.exe" if os.name == "nt" else "python")
            command = bindir / ("spatial-ui.exe" if os.name == "nt" else "spatial-ui")
            subprocess.run([str(python), "-I", "-c", INSTALLED_CHECK], cwd=temporary, check=True)
            for argv in ([str(python), "-I", "-m", "spatial_ui", "--version"],
                         [str(command), "--version"]):
                result = subprocess.run(argv, cwd=temporary, check=True, capture_output=True, text=True)
                assert result.stdout.strip() == f"spatial-ui {version}", result.stdout
            print(f"Verified {artifact.name}", flush=True)


if __name__ == "__main__":
    main()
