import os
import re
import argparse
import subprocess
import collections
from io import StringIO

import semver


class GitNotFound(Exception):
    pass


class GitRepositoryNotFound(Exception):
    pass


class GitRepositoryEmpty(Exception):
    pass


class GitRepositoryBroken(Exception):
    pass


class GitUnexpectedError(Exception):
    pass


class GitCommitNotConventional(Exception):
    pass


class GitTagVersionNotSemVer(Exception):
    pass


def _print_status(proc_name, message, error=False, check_file_path=None, end="\n"):
    print("kyanit-versioning: ", end="")
    if not error:
        print(f"{proc_name}: {message}", end=end)
    else:
        if check_file_path is not None:
            print(
                f"{proc_name} ERROR: {message} (check file '{check_file_path}')",
                end=end,
            )
        else:
            print(f"{proc_name} ERROR: {message}", end=end)


class GitReleaseStatus:
    """
    This class aids in the release process of a project managed in a git repository.

    The commits must follow the Conventional Commits Specification (1.0.0)!
    (https://www.conventionalcommits.org/en/v1.0.0/)

    Version tags MUST follow the Semantic Versioning Specification (2.0.0)!
    (https://semver.org/spec/v2.0.0.html)
    Version tags MUST be incremental in time (following precedence rules) for the next
    release version to be meaningful.

    Version tags MUST start with the letter "v", immediately followed by the version!

    CAVEATS:

    PRERELEASE VERSION TAGS ARE NOT SUPPORTED AND WILL RESULT IN UNEXPECTED ERRORS

    Non-version-tags MUST NEVER start with the letter "v" immediately followed by a
    number (ex. v1-this-is-a-tag). This would also result in unexpected errors.
    """

    def __init__(self, work_dir=None):
        if work_dir is None:
            self.work_dir = os.getcwd()
        else:
            self.work_dir = work_dir

    @property
    def head(self):
        """
        Version string of the checked-out commit (HEAD).

        If working tree is clean and HEAD is on a version-tagged commit with the format
        vMAJOR.MINOR.PATCH (where MAJOR, MINOR and PATCH are positive integers), this
        will be the version (omitting the leading "v" character), otherwise it will be a
        local (dev) version string in the following format:

        If working tree is clean (with no uncomitted changes):
        <version>+<commit_count>.<commit_hash>.clean

        If working tree is dirty (contains uncomitted changes):
        <version>+<commit_count>.<commit_hash>.dirty

        If there are local changes on a version-tagged commit (and it is the checked-out
        commit), head will be in the following format:
        <version>+0.dirty

        Some examples of what `head` might look like:
        2.0.1
        1.1.0+0.dirty
        1.1.0+12.2dfee1f.dirty
        3.0.1+3.8d99ee4.clean
        """

        try:
            proc = subprocess.run(
                [
                    "git",
                    "describe",
                    "--tags",
                    "--match",
                    "v[0-9]*",
                    "--dirty",
                    "--broken",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.work_dir,
            )
        except FileNotFoundError:
            raise GitNotFound

        if proc.stderr:
            if "not a git repository" in proc.stderr.decode():
                raise GitRepositoryNotFound
            elif (
                "no names found" in proc.stderr.decode().lower()
                or "no tags can describe" in proc.stderr.decode().lower()
            ):
                # no version tag exists yet, or existing tags can't describe the commit,
                # get the commit hash instead
                proc = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.work_dir,
                )
                if proc.stderr:
                    if "needed a single revision" in proc.stderr.decode().lower():
                        # no commits yet
                        raise GitRepositoryEmpty
                    else:
                        raise GitUnexpectedError(proc.stderr.decode())
                rev_hash = proc.stdout.decode().strip()

                # get number of commits
                proc = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self.work_dir,
                )
                if proc.stderr:
                    raise GitUnexpectedError(proc.stderr.decode())
                rev_count = proc.stdout.decode().strip()

                # determine if the working tree contains changes
                dirty = bool(
                    subprocess.run(
                        ["git", "diff", "--quiet"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=self.work_dir,
                    ).returncode
                )

                # returned version will be 0.0.0+<num_commits>.<commit_hash>.clean/dirty
                return f"0.0.0+{rev_count}.{rev_hash}.{'dirty' if dirty else 'clean'}"
            else:
                raise GitUnexpectedError(proc.stderr.decode())

        if "-broken" in proc.stdout.decode():
            raise GitRepositoryBroken
        
        match = re.search(
            r"([0-9]+\.[0-9]+\.[0-9]+)(?:\-([0-9]+))?(?:\-g([0-9a-f]+))?(?:-(dirty))?",
            proc.stdout.decode(),
        )

        try:
            version = match.group(1)
        except AttributeError:
            raise GitTagVersionNotSemVer(f"{proc.stdout.decode()}")

        rev_count = match.group(2)
        rev_hash = match.group(3)
        dirty = match.group(4)

        if rev_count:
            if rev_hash is None:
                # this shouldn't normally happen, because if there are commits since the
                # version tag, git should return the commit hash as well
                raise GitUnexpectedError("cannot get hash of current commit")
            return f"{version}+{rev_count}.{rev_hash}.{dirty or 'clean'}"
        elif dirty:
            return f"{version}+0.dirty"
        elif rev_count is None and rev_hash is None and dirty is None:
            return version
        else:
            # this should only happen if git returns an unexpected describe string
            raise GitUnexpectedError("unexpected git describe output")

    @property
    def commits(self):
        """
        An OrderedDict of the git log since the latest release with the following
        scheme:

        {
            "<commit_hash>":
                {
                    "type": <conventional_commit_type>  # without trailing excl. mark
                    "scope": <conventional_commit_scope>  # or None
                    "breaking": <True/False>  # whether the commit is a breaking change
                    "summary": <commit_summary>  # the part after the colon
                    "description": <commit_description>  # rest of the commit, or None
                }
            ...
        }

        First key is the newest commit.
        """

        latest = self.latest
        if latest != "0.0.0":  # there is at least one version tag
            GIT_CMD = ["git", "log", "--no-decorate", "--log-size", f"v{latest}.."]
        else:
            GIT_CMD = ["git", "log", "--no-decorate", "--log-size"]

        git_process = subprocess.Popen(
            GIT_CMD, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        git_output = StringIO(git_process.communicate()[0].decode())
        commits = collections.OrderedDict()

        while True:
            line = git_output.readline()
            if not line:
                break

            revision = re.search(r"commit ([0-9|a-f]+)", line).group(1)
            log_size = int(re.search(r"log size (\d+)", git_output.readline()).group(1))

            commit_body = StringIO(git_output.read(log_size))

            while commit_body.readline().strip():
                pass  # discard commit header

            try:
                conventional_commit = re.match(
                    r"^\s*"  # leading whitespace
                    r"([a-z|A-Z|0-9|\.|\_|\-]+)"  # type
                    r"(?:\(([a-z|A-Z|0-9|\.|\_|\-]+)\))?"  # scope
                    r"(\!)?"  # breaking or not (bang in type)
                    r"\:\s(.*)$",  # summary text
                    commit_body.readline().strip(),
                )
            except AttributeError:
                raise GitCommitNotConventional(revision)

            commit_type = conventional_commit.group(1)
            commit_scope = conventional_commit.group(2)
            commit_breaking = bool(conventional_commit.group(3))
            commit_summary = conventional_commit.group(4)
            # rest of commit body without unnecessary whitespace
            commit_description = re.sub(r"\n\s+", "\n", commit_body.read().strip())

            if (
                "\nBREAKING CHANGE" in commit_description
                or "\nBREAKING-CHANGE" in commit_description
            ):
                commit_breaking = True

            commits[revision] = {
                "type": commit_type,
                "scope": commit_scope,
                "breaking": commit_breaking,
                "summary": commit_summary,
                "description": commit_description or None,
            }

            git_output.readline()  # read empty line after commit body

        return commits  # newest first

    @property
    def latest(self):
        """
        This is the latest (current) release in the format MAJOR.MINOR.PATCH
        representing the latest tag that matches the format vMAJOR.MINOR.PATCH (where
        MAJOR, MINOR and PATCH are positive integers).
        """

        # version portion of the head is the latest version tag (or 0.0.0)
        head = self.head
        try:
            return re.search(r"^([0-9]+\.[0-9]+\.+[0-9]+)", head).group(1)
        except AttributeError:
            raise GitTagVersionNotSemVer(f"head is: {head}")

    @property
    def next(self):
        """
        This is the next (new) release version based on the commit types in the history
        (git log) and the latest (current) version.

        If there's at least one breaking change in the history, the MAJOR version is
        bumped, otherwise if there's at least a `feat` type commit in the history,
        the MINOR version is bumped, otherwise if there's at least a `fix` type commit
        in the history, the PATCH version is bumped. If none of the above
        applies, the next version will be the same as the latest (current) version.

        The only exception to the above is that if the latest MAJOR version is a zero,
        the MINOR version is bumped for breaking changes instead of the MAJOR version.

        Examples (latest version -> next version  # condition):

        1.0.0 -> 1.0.1  # history includes at least one `fix` commit and no breaking
        changes or `feat` commits

        1.0.0 -> 1.1.0  # history includes at least one `feat` commit and no breaking
        changes

        1.0.0 -> 2.0.0  # history includes at least one breaking change

        0.1.0 -> 0.2.0  # history includes at least one `feat` commit or at least one
        breaking change
        """

        try:
            version = semver.parse_version_info(self.latest)
        except ValueError:
            raise GitTagVersionNotSemVer(self.latest)

        commits = self.commits.values()

        for commit in commits:
            if commit["breaking"]:
                if version.major == 0:
                    return str(version.bump_minor())
                else:
                    return str(version.bump_major())

        for commit in commits:
            if commit["type"] == "feat":
                return str(version.bump_minor())

        for commit in commits:
            if commit["type"] == "fix":
                return str(version.bump_minor())

        return version

    def group_commits(self, types=["feat", "fix"]):
        """
        Return a dictionary containing the changes since the latest (current) version,
        grouped by the conventional commit type. Only types included in `types` will be
        included ("feat" and "fix" types by default).

        The returned dictionary will have the following scheme:

        {
            <commit_type>:
                [
                    {
                        "type": <commit_type>  # same as containing key
                        ...  # rest of the keys of the commit from `history`
                        "hash": <commit_hash>  # added (was the commit key in `history`)
                    },
                    ...
                ],
            ...
        }
        """

        # apply filter, preserving feat and fix types at the beginning
        commit_types = set(types)
        grouped_history = {commit_type: [] for commit_type in commit_types}
        commits = self.commits.items()

        for commit in commits:
            if commit[1]["type"] in commit_types:
                grouped_history[commit[1]["type"]].append(commit[1])
                grouped_history[commit[1]["type"]][-1]["hash"] = commit[0]

        return grouped_history


def command_line(*args):
    parser = argparse.ArgumentParser(
        prog="kyanit-versioning",
        description="Command-line application for generating semantic versions and "
        "changelogs based on the git log.",
        usage="%(prog)s [options...]",
    )

    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="print all info about HEAD and history",
    )

    parser.add_argument(
        "-v", "--latest", action="store_true", help="print the latest release version",
    )

    parser.add_argument(
        "-n",
        "--next",
        action="store_true",
        help="calculate the next release version based on the latest release version "
        "and conventional commit history",
    )

    parser.add_argument(
        "-d",
        "--describe",
        action="store_true",
        help="print the local (dev) version string of the HEAD commit if HEAD is not"
        "on a tagged release, in which case print the same as --latest",
    )

    parser.add_argument(
        "-c",
        "--changelog",
        metavar="TYPE",
        nargs="*",
        help='print the changelog since last release; by default only "feat" and "fix" '
        'type commits will be included; this can be overridden with at least one TYPE '
        'passed',
    )

    args = parser.parse_args(*args)

    repo_status = GitReleaseStatus()

    if args.latest or args.all:
        version = repo_status.latest
        if version == "0.0.0":
            version = None
        _print_status("last release", f"{version or 'no release yet.'}")

    if args.next or args.all:
        version = repo_status.next
        if version == repo_status.latest:
            version = None
        _print_status("next release", f"{version or 'next release not needed.'}")

    if args.describe or args.all:
        _print_status("head commit version", repo_status.head)

    if args.changelog or args.all:
        if not args.changelog:
            args.changelog = ["feat", "fix"]
        
        changelog = repo_status.group_commits(args.changelog)

        anything_in_changelog = False
        for type_ in changelog:
            if changelog[type_]:
                anything_in_changelog = True
                break

        if anything_in_changelog:
            _print_status("changelog", "", end="\n\n")

            for commit_type in args.changelog:
                if commit_type in changelog and changelog[commit_type]:
                    print(f"{commit_type}:")
                    for commit in changelog[commit_type]:
                        commit_scope = (
                            f"[{commit['scope']}] " if commit["scope"] else ""
                        )
                        print(
                            f" - {'BREAKING: ' if commit['breaking'] else ''}"
                            f"{commit_scope}{commit['summary']} ({commit['hash'][:8]})"
                        )
                    print()

        aggregate  = ""
        for type_ in args.changelog:
            if not aggregate:
                aggregate = f"{len(changelog[type_])} {type_} commit(s)"
            else:
                aggregate = f"{aggregate}, {len(changelog[type_])} {type_} commit(s)"
        _print_status(
            "changelog",
            f"{aggregate} since last release",
        )
