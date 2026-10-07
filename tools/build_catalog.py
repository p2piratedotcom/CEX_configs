"""Build reproducible pure-Python plugins and the public, credential-free catalog."""

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    plugins = []
    for directory in sorted((ROOT / "plugins").iterdir()):
        config = json.loads((directory / "config.json").read_text())
        # All adapters use one authoritative HTTP core. Existing wire/error
        # policies for MEXC/Gate remain in a small compatibility shim.
        for helper in sorted((ROOT / "shared" / "cex_plugin").glob("*.py")):
            if helper.name == "http_legacy.py":
                continue
            source = ((ROOT / "shared" / "cex_plugin" / "http_legacy.py")
                      if helper.name == "http.py" and config["venue"] in {"MEXC", "GATE"}
                      else helper)
            # MEXC/Gate keep their existing models/validation/state code.
            if config["venue"] in {"MEXC", "GATE"} and helper.name not in {
                "http.py", "http_pool.py", "http_diagnostics.py", "http_resolver.py"}:
                continue
            shutil.copyfile(source, directory / "src" / "cex_plugin" / helper.name)
        bundle = directory / "adapter.zip"
        with ZipFile(bundle, "w", compression=ZIP_DEFLATED) as output:
            for source in sorted((directory / "src").rglob("*.py")):
                item = ZipInfo(
                    source.relative_to(directory / "src").as_posix(),
                    date_time=(2026, 1, 1, 0, 0, 0),
                )
                item.compress_type = ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                output.writestr(item, source.read_bytes())
        plugins.append(
            dict(
                venue=config["venue"],
                version=config["version"],
                config=(directory / "config.json").relative_to(ROOT).as_posix(),
                adapter=bundle.relative_to(ROOT).as_posix(),
                config_sha256=digest(directory / "config.json"),
                adapter_sha256=digest(bundle),
            )
        )
    (ROOT / "catalog.json").write_text(
        json.dumps(
            dict(
                schema=1,
                protocol=1,
                license_sha256=digest(ROOT / "LICENSE"),
                plugins=plugins,
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
