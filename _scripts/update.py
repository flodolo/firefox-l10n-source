# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import tomli_w
import tomllib
from argparse import ArgumentParser
from filecmp import cmp
from os import makedirs
from os.path import abspath, dirname, exists, join, relpath
from re import match, sub
from shutil import copy
from sys import exit
from compare_locales.merge import merge_channels
from compare_locales.parser import Entity, getParser
from compare_locales.paths import ProjectFiles, TOMLParser

HEAD = "master"

description = f"""
Update the localization source files from a Firefox branch, adding new files and messages.
For updates from the "{HEAD}" branch, also update changed messages.

Writes a summary of the branch's localized files and message keys as `_data/[branch].json`,
and a commit message summary as `.update_msg`.
"""


def add_config(fx_root: str, fx_cfg_path: str, done: set[str]):
    parts = fx_cfg_path.split("/")
    if (
        "{" in fx_cfg_path
        or len(parts) < 3
        or parts[-2] != "locales"
        or parts[-1] != "l10n.toml"
    ):
        exit(f"Unsupported config path: {fx_cfg_path}")

    cfg_path = join("_configs", f"{'-'.join(parts[:-2])}.toml")
    if cfg_path not in done:
        done.add(cfg_path)
        with open(join(fx_root, fx_cfg_path), "rb") as file:
            cfg = tomllib.load(file)
        cfg["basepath"] = ".."
        for path in cfg["paths"]:
            path["reference"] = sub(r"{\s*\S+\s*}", "", path["l10n"])
        if "includes" in cfg:
            for incl in cfg["includes"]:
                incl["path"] = add_config(fx_root, incl["path"], done)

        makedirs(dirname(cfg_path), exist_ok=True)
        with open(cfg_path, "wb") as file:
            tomli_w.dump(cfg, file)
    return cfg_path


def update_str(branch: str, fx_root: str, config_files: list[str]):
    if branch not in [HEAD, "beta", "release"] and not match("esr[0-9]+", branch):
        exit(f"Unknown branch: {branch}")
    if not exists(fx_root):
        exit(f"Firefox root not found: {fx_root}")
    print(f"source: {branch} at {fx_root}")

    configs = []
    fixed_configs = set()
    for cfg_name in config_files:
        cfg_path = join(fx_root, cfg_name)
        if not exists(cfg_path):
            exit(f"Config file not found: {cfg_path}")
        configs.append(TOMLParser().parse(cfg_path))
        if branch == HEAD:
            add_config(fx_root, cfg_name, fixed_configs)

    messages = {}
    new_files = 0
    updated_files = 0
    for fx_path, *_ in ProjectFiles(None, configs):
        rel_path = relpath(fx_path, abspath(fx_root)).replace("/locales/en-US", "")
        makedirs(dirname(rel_path), exist_ok=True)

        try:
            fx_parser = getParser(fx_path)
        except UserWarning:  # No parser found
            messages[rel_path] = []
            if not exists(rel_path):
                print(f"create {rel_path}")
                copy(fx_path, rel_path)
                new_files += 1
            elif branch == HEAD and not cmp(fx_path, rel_path):
                print(f"update {rel_path}")
                copy(fx_path, rel_path)
                updated_files += 1
            else:
                # print(f"skip {rel_path}")
                pass
            continue

        fx_parser.readFile(fx_path)
        fx_res = [entity for entity in fx_parser.walk()]
        messages[rel_path] = [
            entity.key for entity in fx_res if isinstance(entity, Entity)
        ]

        if not exists(rel_path):
            print(f"create {rel_path}")
            copy(fx_path, rel_path)
            new_files += 1
        elif cmp(fx_path, rel_path):
            # print(f"equal {rel_path}")
            pass
        else:
            with open(rel_path, "+rb") as file:
                l10n_data = file.read()
                merge_data = merge_channels(
                    rel_path,
                    [fx_res, l10n_data] if branch == HEAD else [l10n_data, fx_res],
                )
                if merge_data == l10n_data:
                    # print(f"unchanged {rel_path}")
                    pass
                else:
                    print(f"update {rel_path}")
                    file.seek(0)
                    file.write(merge_data)
                    file.truncate()
                    updated_files += 1

    data_path = join("_data", f"{branch}.json")
    makedirs(dirname(data_path), exist_ok=True)
    with open(data_path, "w") as file:
        json.dump(messages, file, indent=2)

    return new_files, updated_files


def write_commit_msg(args, new_files: int, updated_files: int):
    new_str = f"{new_files} new" if new_files else ""
    update_str = f"{updated_files} updated" if updated_files else ""
    summary = (
        f"{new_str} and {update_str}"
        if new_str and update_str
        else new_str or update_str or "no changes"
    )
    count = updated_files or new_files
    summary += " files" if count > 1 else " file" if count == 1 else ""
    head = f"{args.branch} ({args.commit})" if args.commit else args.branch
    with open(".update_msg", "w") as file:
        file.write(f"{head}: {summary}")


if __name__ == "__main__":
    prog = "python -m _scripts.update"
    parser = ArgumentParser(
        prog=prog,
        description=description,
        epilog=f"""Example: {prog} --branch release --firefox ../firefox
        --configs browser/locales/l10n.toml mobile/android/locales/l10n.toml""",
    )
    parser.add_argument(
        "--branch",
        required=True,
        help='The branch identifier, e.g. "master", "beta", or "release".',
    )
    parser.add_argument("--commit", help="A commit id for the branch.")
    parser.add_argument(
        "--firefox", required=True, help="Path to the root of the Firefox source tree."
    )
    parser.add_argument(
        "--configs",
        metavar="C",
        nargs="+",
        required=True,
        help="Relative paths from the Firefox root to the l10n.toml files.",
    )
    args = parser.parse_args()

    update = update_str(args.branch, args.firefox, args.configs)
    write_commit_msg(args, *update)
