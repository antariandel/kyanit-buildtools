import os
import subprocess
from setuptools import setup


def describe_head():
    """
    Return version string of current commit.

    If the current commit is version-tagged, and working tree is clean, return tagged
    version as-is, otherwise append .postN.dev0 to the version, where N is the number of
    commits since the latest version. N is incremented by 1, if the working tree is not
    clean.

    Returned value does not contain the "v" version prefix.

    `Exception('working tree broken')` is raised if the working tree is broken.
    """

    try:
        git_describe = (
            subprocess.check_output('git describe --tags --match "v*" --dirty --broken')
            .decode()
            .strip()[1:]
        )
    except subprocess.CalledProcessError:
        return "0.0.0.dev0"
    if "-broken" in git_describe:
        raise Exception("working tree broken")
    match = re.match(r"([0-9]+\.[0-9]+\.[0-9]+)(\-([0-9+]))?", git_describe)
    if match.group(2) is None:
        # HEAD on tagged commit, append .post1.dev0, if working tree is not clean
        return "{}{}".format(
            match.group(1), ".post1.dev0" if "-dirty" in git_describe else ""
        )
    else:
        # HEAD not on tagged commit, append .postN.dev0, incrementing N if working tree
        # is dirty
        return "{}.post{}.dev0".format(
            match.group(1), int(match.group(3)) + 1 if "-dirty" in git_describe else 0
        )


# write version to kyanit_buildtools._version
with open(os.path.join("kyanit_buildtools", "_version.py"), "w") as file:
    file.write("__version__ = '{}'".format(describe_head()))


setup(version=describe_head())
