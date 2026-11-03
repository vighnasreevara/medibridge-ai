"""Synthea cardiac cohort: generate (Java) and read the FHIR R4 bundles.

    uv run python -m medibridge_seed.synthea generate      # downloads Synthea once, then generates the cohort

Synthea (https://github.com/synthetichealth/synthea, Apache-2.0) simulates fictional patients' lifetimes with
disease modules (atrial fibrillation, heart failure, coronary disease, myocardial infarction, hypertension,
hyperlipidemia ...). A keep module (database/synthea/keep_cardiac.json) keeps only patients with an active
cardiac diagnosis. Generation is seeded, so the same inputs give the same cohort.

The output stays in var/synthea/ (git-ignored). The seed imports it through ``read_bundles``, which keeps the
cardiology-relevant part of each record only (see ``cohort_import``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from medibridge_api.config import REPO_ROOT

VERSION = "4.0.0"
JAR_URL = f"https://github.com/synthetichealth/synthea/releases/download/v{VERSION}/synthea-with-dependencies.jar"
# Published by GitHub for the v4.0.0 release asset; the download is refused if it does not match.
JAR_SHA256 = "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
TOOLS = Path(REPO_ROOT) / "var" / "tools" / "synthea"
JAR = TOOLS / f"synthea-with-dependencies-{VERSION}.jar"
OUTPUT = Path(REPO_ROOT) / "var" / "synthea" / "output"
KEEP_MODULE = Path(REPO_ROOT) / "database" / "synthea" / "keep_cardiac.json"
SEED = 20261022
POPULATION = 60
AGES = "50-88"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def ensure_jar() -> Path:
    if JAR.exists() and _sha256(JAR) == JAR_SHA256:
        return JAR
    TOOLS.mkdir(parents=True, exist_ok=True)
    tmp = JAR.with_suffix(".part")
    print(f"Downloading Synthea {VERSION} (about 200 MB) ...")
    urllib.request.urlretrieve(JAR_URL, tmp)  # noqa: S310 - fixed https URL
    digest = _sha256(tmp)
    if digest != JAR_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Synthea download checksum mismatch ({digest}); refusing to run it")
    tmp.replace(JAR)
    return JAR


def generate(population: int = POPULATION, reference_date: str | None = None) -> Path:
    """Run Synthea; returns the FHIR output directory. Replaces any previous output."""
    if shutil.which("java") is None:
        raise RuntimeError("Java 17+ is required to generate the Synthea cohort (winget install Microsoft.OpenJDK.21)")
    jar = ensure_jar()
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    args = ["java", "-jar", str(jar), "-p", str(population), "-s", str(SEED), "-cs", str(SEED), "-a", AGES,
            "-k", str(KEEP_MODULE), f"--exporter.baseDirectory={OUTPUT}", "--exporter.years_of_history=10",
            "--exporter.fhir.export=true", "--exporter.hospital.fhir.export=false",
            "--exporter.practitioner.fhir.export=false", "--generate.only_alive_patients=true"]
    if reference_date:
        args += ["-r", reference_date.replace("-", "")]
    args.append("Massachusetts")
    subprocess.run(args, cwd=TOOLS, check=True)
    (OUTPUT / "generation.json").write_text(json.dumps(
        {"synthea_version": VERSION, "jar_sha256": JAR_SHA256, "seed": SEED, "population": population,
         "ages": AGES, "keep_module": KEEP_MODULE.name, "reference_date": reference_date}, indent=1), encoding="utf-8")
    return OUTPUT / "fhir"


def read_bundles(directory: Path | None = None) -> Iterator[list[dict]]:
    """Each patient's FHIR resources, in a stable order (sorted by file name)."""
    directory = directory or OUTPUT / "fhir"
    files = sorted(p for p in directory.glob("*.json") if not p.name.startswith(("hospital", "practitioner")))
    if not files:
        raise FileNotFoundError(f"No Synthea output in {directory}. Run: uv run python -m medibridge_seed.synthea generate")
    for path in files:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        yield [e["resource"] for e in bundle.get("entry", [])]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["generate"])
    parser.add_argument("--population", type=int, default=POPULATION)
    parser.add_argument("--reference-date", help="YYYY-MM-DD the simulation ends on (default: today)")
    args = parser.parse_args()
    out = generate(args.population, args.reference_date)
    print(f"Synthea cohort written to {out} ({len(list(out.glob('*.json')))} patients)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
