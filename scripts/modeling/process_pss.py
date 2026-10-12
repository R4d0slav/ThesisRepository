"""
processPSS.py — Parse a PSS SBML/XML network file and write species/reactions to JSON.

Usage:
    python processPSS.py --input <network.xml> [--output <dir>]
"""

from bs4     import BeautifulSoup, Tag  # type: ignore
from pathlib import Path

import argparse
import json
import sys


# Species ID suffix → biological type.
# IDs follow: s_<n>_<compartment>_<type_code>[_<index>]
SPECIE_TYPE: dict[str, str] = {
    "ab":    "abiotic",
    "abst":  "foreign_entity",
    "absta": "foreign_entity",
    "c":     "complex",
    "ca":    "complex_active",
    "f":     "foreign_entity",
    "g":     "gene",
    "m":     "metabolite",
    "mi":    "mirna",
    "mr":    "mrna",
    "nc":    "ncrna",
    "p":     "protein",
    "pa":    "protein_active",
    "pr":    "process",
    "pra":   "process_active",
}


def specie_type(tag: Tag) -> str:
    """Resolve biological type from a species ID suffix, skipping trailing indices.
    e.g. 's_LHB1_cyt_p_1' → 'p' → 'protein'
    """
    code = next(s for s in reversed(tag["id"].split("_")) if not s.isdigit())
    return SPECIE_TYPE.get(code, "unknown")


def refs(reaction: Tag, list_tag: str, ref_tag: str) -> list[str]:
    """Return species IDs from a reaction's listOf* section, or [] if absent."""
    section = reaction.find(list_tag)
    return [r[ref_tag] for r in section.find_all(True)] if section else []


def parse(path: Path) -> tuple[dict, dict]:
    """Parse a PSS XML file into (species, reactions) dicts."""
    soup = BeautifulSoup(path.read_text(), "xml")

    species = {
        s["id"]: {
            "name":        s.get("name", ""),
            "compartment": s.get("compartment", ""),
            "type":        specie_type(s),
        }
        for s in soup.find_all("species")
    }

    reactions = {
        r["id"]: {
            "reactants": refs(r, "listOfReactants", "species"),
            "products":  refs(r, "listOfProducts",  "species"),
            "modifiers": refs(r, "listOfModifiers", "species"),
        }
        for r in soup.find_all("reaction")
    }

    return species, reactions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",  required=True, help="Path to PSS SBML/XML file.")
    parser.add_argument("--output", default=".",   help="Output directory (default: cwd).")
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)

    if not src.exists():
        sys.exit(f"Error: '{src}' not found.")

    dst.mkdir(parents=True, exist_ok=True)
    species, reactions = parse(src)

    (dst / "species.json").write_text(json.dumps(species, indent=2))
    (dst / "reactions.json").write_text(json.dumps(reactions, indent=2))

    print(f"Saved {len(species):>5} species   → {dst / 'species.json'}")
    print(f"Saved {len(reactions):>5} reactions → {dst / 'reactions.json'}")


if __name__ == "__main__":
    main()