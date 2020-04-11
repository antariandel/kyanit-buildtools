import re
import argparse
import subprocess
import collections
from io import StringIO

import semver


def get_commit_history(until_rev=None):
    """
    Return an OrderedDict of the git log, where the key is the revision number.
    First key is the newest commit.

    If `until_rev` is not `None`, return log up to the revision that matches
    `until_rev`.
    """

    if until_rev is not None:
        GIT_CMD = 'git log --no-decorate --log-size "v{}"..'.format(until_rev)
    else:
        GIT_CMD = "git log --no-decorate --log-size"

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
    GIT_CMD = "git describe --tags --abbrev=0"
    try:
        last_tag = (
            subprocess.check_output(GIT_CMD, stderr=subprocess.DEVNULL).decode().strip()
        )
    except subprocess.CalledProcessError:
        # can not describe (probably no tags yet)
        return None
    if not last_tag.startswith("v"):
        raise RuntimeError("last git tag is not a version tag")
    version = last_tag[1:]
    try:
        semver.parse(version)
    except ValueError:
        raise RuntimeError("last version tag is not valid SemVer")
    return version


def get_commit_oneline(commit):
    commit = StringIO(commit)
    while True:
        for i in range(3):
            commit.readline()  # discard author and date
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
        for i in range(3):
            commit_info.readline()  # discard author and date
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


def get_changelog():
    def write_rev_info(stringio, commits, rev):
        stringio.write(
            " - {scope}{oneline} ({rev})\n".format(
                scope="[{}] ".format(commit_types[rev][1])
                if commit_types[rev][1] is not None
                else "",
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

    changelog.write("Features:\n\n")
    for rev in features:
        write_rev_info(changelog, commits, rev)
    changelog.write("\nFixes:\n\n")
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
        prog="genrelease",
        description="Kyanit Build Tools - genrelease: Version and changelist generator",
        usage="python -m kyanit_buildtools.%(prog)s [options...]"
    )

    parser.add_argument(
        "--latest-version", "-lv",
        action="store_true",
        help="print latest repository version"
    )

    parser.add_argument(
        "--new-version", "-nv",
        action="store_true",
        help="calculate new verson based on latest version and conventional commit "
             "history, or return latest version if no feature or fix type commits "
             "happened since"
    )

    parser.add_argument(
        "--print-changelog", "-cl",
        action="store_true",
        help="print changelog since last release based on conventional commit history; "
             "the log will contain only feature and fix type commit messages"
    )

    parser.add_argument(
        "--write-changelog", "-wcl",
        nargs="?",
        metavar="FILE",
        help="write changelog to file"
    )

    parser.add_argument(
        "--create-version-file", "-vf",
        nargs="?",
        metavar="FILE",
        help="create a Python script containing '__version__ = VER' where VER will "
             "be the version returned by --new-version if feature of fix changes have "
             "been made since --latest-version, otherwise put --latest-version in VER"
    )

    args = parser.parse_args()

    if args.latest_version:
        version = get_latest_version()
        if version is None:
            print("0.0.0")
        else:
            print(version)

    if args.new_version:
        version = get_new_version()
        if version is None:
            version = get_latest_version()

        if version is None:
            print("0.0.0")
        else:
            print(version)

    if args.print_changelog:
        print(get_changelog())

    if args.write_changelog:
        with open(args.write_changelog, "w") as file:
            file.write(get_changelog())

    if args.create_version_file:
        version = get_new_version()
        if version is None:
            version = get_latest_version()

        if version is None:
            version = "0.0.0"

        with open(args.create_version_file, "w") as file:
            file.write("__version__ = \"{}\"".format(version))
