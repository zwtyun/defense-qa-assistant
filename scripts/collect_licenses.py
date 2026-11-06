"""Copy installed dependency license notices for the portable delivery."""
from importlib import metadata
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "third_party_licenses"
target.mkdir(exist_ok=True)
index = ["第三方依赖许可记录（按已安装发行包复制）\n"]
versions = []
for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata.get("Name") or "").lower()):
    name = dist.metadata.get("Name") or "unknown"
    version = dist.version
    versions.append(name + "==" + version)
    license_text = dist.metadata.get("License-Expression") or dist.metadata.get("License") or "See package notices"
    index.append(name + " " + version + " — " + license_text[:250])
    for file in dist.files or []:
        if not re.match(r"(?i)^(license|licence|copying|notice)([._-]|$)", file.name):
            continue
        source = Path(dist.locate_file(file))
        if source.is_file() and source.stat().st_size < 2000000:
            filename = re.sub(r"[^\w.-]", "_", name + "_" + str(file)) + ".txt"
            (target / filename).write_bytes(source.read_bytes())
(target / "INDEX.txt").write_text("\n".join(index), encoding="utf-8")
(ROOT / "已验证依赖版本.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")
print("License notices:", len(list(target.glob("*.txt"))))
