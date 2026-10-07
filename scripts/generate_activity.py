import argparse
import json
import random
import time
from uuid import uuid4

from app.core.config import Settings
from app.core.paths import RootPaths


def generate(
    profile: str,
    paths: RootPaths,
    interval: float,
    seed: int = 42,
    count: int | None = None,
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
    count = count if count is not None else 6 if profile == "normal" else 100
    names = [f"{run}/sample-{index:03}.{'csv' if index % 2 else 'txt'}" for index in range(count)]
    for name in names:
        with demo_paths.local(name).open("xb") as output:
            output.write(rng.randbytes(rng.randint(128, 2048)))
    time.sleep(interval)
    for name in names:
        with demo_paths.local(name).open("ab") as output:
            output.write(rng.randbytes(512))
    time.sleep(interval)
    for name in names[: count // 2]:
        destination = name + ".moved"
        demo_paths.local(name).rename(demo_paths.local(destination))
    time.sleep(interval)
    for name in names[count // 2 :]:
        demo_paths.local(name).unlink()
    time.sleep(interval)
    return {
        "profile": profile,
        "created": count,
        "modified": count,
        "moved": count // 2,
        "deleted": count // 2,
        "directory": f"demo/{run}",
    }


def baseline(paths: RootPaths, interval: float, seed: int, minutes: int) -> None:
    """Collect real, varied minute-spaced history without changing event timestamps."""
    if not 1 <= minutes <= 120:
        raise ValueError("minutes must be between 1 and 120")
    if not 2 * Settings().poll_interval <= interval < 10:
        raise ValueError("baseline interval must cover two polls and be below 10 seconds")
    rng = random.Random(seed)
    # Start each round just after a UTC minute boundary; finish before the next.
    boundary = (int(time.time()) // 60 + 1) * 60 + 2
    for index in range(minutes):
        time.sleep(max(0, boundary - time.time()))
        result = generate("normal", paths, interval, seed + index, rng.randint(3, 12))
        print(json.dumps({"round": index + 1, **result}), flush=True)
        boundary += 60
    time.sleep(max(0, boundary - time.time()))  # Last bucket must be complete.


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate activity only inside WATCH_ROOT/demo")
    parser.add_argument("--profile", choices=["normal", "burst", "baseline"], default="normal")
    parser.add_argument("--interval", type=float, default=2.5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--minutes", type=int, default=31, help="baseline rounds (default: 31)")
    args = parser.parse_args()
    try:
        paths = RootPaths(Settings().watch_root)
        if args.profile == "baseline":
            baseline(paths, args.interval, args.seed, args.minutes)
            return
        result = generate(args.profile, paths, args.interval, args.seed)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"generator failed: {type(exc).__name__}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
