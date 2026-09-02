from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import uuid4

import tomlkit


def load(path: Path):
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def save(path: Path, document) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(tomlkit.dumps(document), encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--group")
    parser.add_argument("--platform")
    parser.add_argument("--content-file", type=Path)
    parser.add_argument("--image-path", default="")
    args = parser.parse_args()
    path = args.config.resolve(strict=True)
    document = load(path)
    section = document["announcement_administration"]
    if args.status:
        print("true" if bool(section.get("execute_send")) else "false")
        return
    if not args.group or not args.platform or args.content_file is None:
        parser.error("queue mode requires --group, --platform and --content-file")
    content = args.content_file.resolve(strict=True).read_text(encoding="utf-8").strip()
    section["group_id"] = args.group
    section["platform"] = args.platform
    section["content"] = content
    section["image_path"] = args.image_path
    section["execute_send"] = True
    save(path, document)
    print(f"queued:{args.platform}:{args.group}")


if __name__ == "__main__":
    main()
