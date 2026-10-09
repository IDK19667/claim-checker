"""
Pack the fly-through's lores and motion tiers into one file each.

The page needs the lores and motion frames before the footage can play,
and fetching them one by one is 1,104 requests. On a free Render instance
that answers a handful at a time, those requests, not the bytes, are what
kept a first visit on "Loading footage" for half a minute. One file per
tier is one request, which the page reads as it streams in and cuts into
frames by the offsets written here.

The per-frame files stay where they are: the page falls back to them if a
bundle fails part-way, and an old page cached before this still asks for
them. Run after scripts/build_flight.py (which calls this itself):

    .venv/bin/python scripts/bundle_flight.py
"""
import json
import pathlib

FRAMES = pathlib.Path(__file__).resolve().parent.parent / "static" / "flight"
MANIFESTS = ("manifest.json", "manifest-2560.json", "manifest-phone.json")


def bundle(pattern: str, count: int) -> tuple[str, list[int]]:
    """Concatenate one tier's frames in order. Returns (file name, offsets),
    where frame i is bytes offsets[i] to offsets[i + 1]."""
    tier = pattern.split("/")[0]
    name = f"{tier}.bundle"
    offsets, parts = [0], []
    for i in range(count):
        data = (FRAMES / pattern.replace("%04d", f"{i:04d}")).read_bytes()
        parts.append(data)
        offsets.append(offsets[-1] + len(data))
    (FRAMES / name).write_bytes(b"".join(parts))
    return name, offsets


def main() -> None:
    done: dict[str, tuple[str, list[int]]] = {}
    for m in MANIFESTS:
        path = FRAMES / m
        manifest = json.loads(path.read_text())
        for key in ("loresPattern", "motionPattern"):
            pattern = manifest[key]
            if pattern not in done:
                done[pattern] = bundle(pattern, manifest["count"])
                name, offs = done[pattern]
                print(f"{name}: {manifest['count']} frames, {offs[-1] / 1e6:.2f}MB")
            name, offs = done[pattern]
            tier = key[:-len("Pattern")]
            manifest[f"{tier}Bundle"] = name
            manifest[f"{tier}Offsets"] = offs
        path.write_text(json.dumps(manifest, indent=1) + "\n")


if __name__ == "__main__":
    main()
