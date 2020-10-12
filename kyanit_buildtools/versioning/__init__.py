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


class GitCommitNeeded(Exception):
    pass


class GitWorkingTreeBroken(Exception):
    pass


class GitUnexpectedError(Exception):
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


def describe_head():
    """
    Create version string of the current commit.

    If working tree is clean and HEAD is on a version tagged commit, return the version,
    otherwise return a local (dev) version string in the following format:

    <version>+<commit_count>.<commit_hash>.clean/dirty

    Examples:

    2.0.1
    1.1.0+12.2dfee1f.dirty
    3.0.1+3.8d99ee4.clean
    """

    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--match", "v*", "--dirty", "--broken"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise GitNotFound

    if proc.stderr:
        if "not a git repository" in proc.stderr.decode():
            raise GitRepositoryNotFound
        elif "cannot describe anything" in proc.stderr.decode():
            # no version tag exists yet
            proc = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if proc.stderr:
                if "needed a single revision" in proc.stderr.decode().lower():
                    raise GitCommitNeeded
                else:
                    raise GitUnexpectedError(proc.stderr.decode())
            rev_hash = proc.stdout.decode().strip()

            proc = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if proc.stderr:
                raise GitUnexpectedError(proc.stderr.decode())
            rev_count = proc.stdout.decode().strip()

            dirty = bool(
                subprocess.run(
                    ["git", "diff", "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            )

            # returned version will be 0.0.0+<num_commits>.<commit_hash>.clean/dirty
            return f"0.0.0+{rev_count}.{rev_hash}.{'dirty' if dirty else 'clean'}"
        else:
            raise GitUnexpectedError(proc.stderr.decode())

    if "-broken" in proc.stdout.decode():
        raise GitWorkingTreeBroken

    match = re.search(
        r"([0-9]+\.[0-9]+\.[0-9]+)(?:\-([0-9]+))?(?:\-g([0-9a-f]+))?(?:-(dirty))?",
        proc.stdout.decode(),
    )

    version = match.group(1)
    rev_count = match.group(2)
    rev_hash = match.group(3)
    dirty = match.group(4)

    if rev_count:
        if rev_hash is None:
            # this shouldn't ever happen
            raise GitUnexpectedError("cannot get hash of current commit")
        return f"{version}+{rev_count}.{rev_hash}.{dirty or 'clean'}"
    else:
        return version


def get_commit_history(until_rev=None):
    """
    Return an OrderedDict of the git log, where the key is the revision number.
    First key is the newest commit.

    If `until_rev` is not `None`, return log up to the revision that matches
    `until_rev`.
    """

    if until_rev is not None:
        GIT_CMD = ["git", "log", "--no-decorate", "--log-size", f"v{until_rev}.."]
    else:
        GIT_CMD = ["git", "log", "--no-decorate", "--log-size"]

    git_process = subprocess.Popen(
        GIT_CMD, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    git_output = git_process.communicate()[0]
    git_output = StringIO(git_output.decode())

    commits = collections.OrderedDict()
    while True:
        line = git_output.readline()
        if not line:
            break
        revision = re.match(r"commit ([0-9|a-f]+)", line).group(1)
        log_size = int(re.match(r"log size (\d+)", git_output.readline()).group(1))
        commit_info = git_output.read(log_size)
        commits[revision] = commit_info
        git_output.readline()  # read empty line after commit
    return commits  # newest first


def get_latest_version():
    GIT_CMD = ["git", "describe", "--tags", "--match", "v*", "--abbrev=0"]
    try:
        last_tag = (
            subprocess.check_output(GIT_CMD, stderr=subprocess.DEVNULL).decode().strip()
        )
    except subprocess.CalledProcessError:
        # can not describe (probably no tags yet)
        return None
    version = last_tag[1:]
    try:
        semver.parse(version)
    except ValueError:
        raise RuntimeError("last version tag is not valid SemVer")
    return version


def get_commit_oneline(commit):
    commit = StringIO(commit)
    while True:
        while commit.readline().strip():
            pass  # discard commit header
        line = commit.readline().strip()
        return line.partition(":")[2].strip() if ":" in line else line


def get_commit_types(commits, include=[]):
    """
    Return an OrderedDict of commit types extracted from `commits`, where the key is the
    revision number. First key is the newest commit.

    If `include` is not empty, only commits with the types listed in `include` will be
    returned.
    """

    commits_out = collections.OrderedDict()
    for revision in commits:
        commit_info = StringIO(commits[revision])
        while commit_info.readline().strip():
            pass  # discard commit header
        commit_line = commit_info.readline()
        commit_type = re.match(r"\s+([a-z]+)[(|!|:]", commit_line).group(1)
        commit_scope = re.search(r"\((.*)\):", commit_line)
        if include and commit_type not in include:
            continue
        while True:
            line = commit_info.readline()
            if not line:
                break
            if "BREAKING CHANGE" in line or "BREAKING-CHANGE" in line:
                commit_type = "{}!".format(commit_type)
                break
        commits_out[revision] = (
            commit_type,
            None if commit_scope is None else commit_scope.group(1),
        )
    return commits_out  # newest first


def bump_version_from_hist(start_version, commit_types):
    """
    Return a new SemVer starting from `start_version` based on the `commit_types`.

    Starting at `start_version`, bump a major version if there's at least one breaking
    change in the commit type history, and return it. Otherwise bump a minor version if
    there's at least one feature type change in the history, and return it. Otherwise
    bump a patch version, if there's at least one fix type change in the history, and
    return it. Return `start_version` if none of the previous applies.
    """

    version = semver.parse_version_info(start_version)
    commit_types = [commit_type[0] for commit_type in commit_types.values()]
    for commit_type in commit_types:
        if "!" in commit_type:
            if version.major == 0:
                return str(version.bump_minor())
            else:
                return str(version.bump_major())
    if "feat" in commit_types:
        return str(version.bump_minor())
    if "fix" in commit_types:
        return str(version.bump_patch())
    return start_version


def get_change_counts():
    latest_version = get_latest_version()
    if not latest_version:
        commits = get_commit_history()
    else:
        commits = get_commit_history(until_rev=latest_version)
    commit_types = get_commit_types(commits)

    features = 0
    fixes = 0

    for rev in commit_types:
        if commit_types[rev][0].startswith("feat"):
            features += 1
        elif commit_types[rev][0].startswith("fix"):
            fixes += 1

    return (features, fixes)


def get_changelog():
    def write_rev_info(stringio, commits, rev):
        stringio.write(
            " - {scope}{breaking}{oneline} ({rev})\n".format(
                scope="[{}] ".format(commit_types[rev][1])
                if commit_types[rev][1] is not None
                else "",
                breaking="BREAKING: " if "!" in commit_types[rev][0] else "",
                oneline=get_commit_oneline(commits[rev]),
                rev=rev[:8],
            )
        )

    latest_version = get_latest_version()
    if not latest_version:
        commits = get_commit_history()
    else:
        commits = get_commit_history(until_rev=latest_version)
    commit_types = get_commit_types(commits)

    features = []
    fixes = []

    for rev in commit_types:
        if commit_types[rev][0].startswith("feat"):
            features.append(rev)
        elif commit_types[rev][0].startswith("fix"):
            fixes.append(rev)

    changelog = StringIO()

    if features:
        changelog.write("features:\n")
        for rev in features:
            write_rev_info(changelog, commits, rev)
    if fixes:
        changelog.write("fixes:\n")
        for rev in fixes:
            write_rev_info(changelog, commits, rev)

    return changelog.getvalue()


def get_new_version():
    latest_version = get_latest_version()
    if not latest_version:
        latest_version = "0.0.0"
        commits = get_commit_history()
    else:
        commits = get_commit_history(until_rev=latest_version)
    commit_types = get_commit_types(commits)
    new_version = bump_version_from_hist(latest_version, commit_types)

    if new_version != latest_version:
        return new_version

    return None


def command_line():
    parser = argparse.ArgumentParser(
        prog="kyanit-versioning",
        description="Command-line application for generating semantic versions and "
        "changelogs based on git commit history.",
        usage="%(prog)s [options...]",
    )

    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="print all info on the current commit",
    )

    parser.add_argument(
        "-v",
        "--last",
        action="store_true",
        help="print the last tagged version",
    )

    parser.add_argument(
        "-n",
        "--next",
        action="store_true",
        help="calculate the next version based on latest version and conventional "
        "commit history, or return latest version if no feature or fix type commits "
        "happened since",
    )

    parser.add_argument(
        "-d",
        "--describe",
        action="store_true",
        help="create a version string of the current commit",
    )

    parser.add_argument(
        "-c",
        "--changelog",
        action="store_true",
        help="print changelog since last release based on conventional commit history; "
        "the log will contain only feature and fix type commit messages",
    )

    parser.add_argument(
        "-w",
        "--write-changelog",
        nargs="?",
        metavar="FILE",
        help="write the changelog to a file",
    )

    args = parser.parse_args()

    if args.last or args.all:
        version = get_latest_version()
        _print_status("last release", f"{version or 'no release yet.'}")

    if args.next or args.all:
        version = get_new_version()
        _print_status("next release", f"{version or 'next release not needed.'}")

    if args.describe or args.all:
        _print_status("commit version", describe_head())

    if args.changelog or args.all:
        changelog = get_changelog()
        change_counts = get_change_counts()
        if changelog:
            _print_status(
                "changelog",
                "",
                end="\n\n"
            )
            print(get_changelog())
            _print_status(
                "changelog",
                f"{change_counts[0]} features and {change_counts[1]} fixes in total."
            )
        else:
            _print_status("changelog", "no features or fixes since last release.")

    if args.write_changelog:
        with open(args.write_changelog, "w") as file:
            file.write(get_changelog())
