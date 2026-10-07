import argparse
import json
import random
import time
from uuid import uuid4

from app.core.config import Settings
from app.core.paths import RootPaths


def generate(
    profile: str, paths: RootPaths, interval: float, seed: int = 42,
) -> dict[str, str | int]:
    if profile not in {"normal", "burst"}:
        raise ValueError("unsupported profile")
    if interval < 2 * Settings().poll_interval:
        raise ValueError("interval must be at least twice POLL_INTERVAL")
    # Only this dedicated directory is writable in the one-off container.
    demo = paths.local("demo")
    demo.mkdir(exist_ok=True)
    demo_paths = RootPaths(demo)
    run = f"run-{uuid4().hex[:12]}"
    demo_paths.local(run).mkdir()
    rng = random.Random(seed)
    count = 6 if profile == "normal" else 100
    names = [f"{run}/sample-{index:03}.{'csv' if index % 2 else 'txt'}" for index in range(count)]
    for name in names:
        with demo_paths.local(name).open("xb") as output:
            output.write(rng.randbytes(rng.randint(128, 2048)))
    time.sleep(interval)
    for name in names:
        with demo_paths.local(name).open("ab") as output:
            output.write(rng.randbytes(512))
    time.sleep(interval)
    for name in names[:count // 2]:
        destination = name + ".moved"
        demo_paths.local(name).rename(demo_paths.local(destination))
    time.sleep(interval)
    for name in names[count // 2:]:
        demo_paths.local(name).unlink()
    time.sleep(interval)
    return {"profile": profile, "created": count, "modified": count,
            "moved": count // 2, "deleted": count // 2, "directory": f"demo/{run}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate activity only inside WATCH_ROOT/demo")
    parser.add_argument("--profile", choices=["normal", "burst"], default="normal")
    parser.add_argument("--interval", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        result = generate(args.profile, RootPaths(Settings().watch_root), args.interval, args.seed)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"generator failed: {type(exc).__name__}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
