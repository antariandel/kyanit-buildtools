import io
import os
import stat
import errno
import shutil
import pathlib
import argparse
import subprocess

import semver

from ..versioning import GitReleaseStatus

ESP_OPEN_SDK_URL = "https://github.com/kyanit-project/esp-open-sdk"
ESP_OPEN_SDK_REV = "fd14e15"
MICROPYTHON_URL = "https://github.com/micropython/micropython"
MICROPYTHON_REV = "42342fa"
WORK_DIR = os.path.join(pathlib.Path.home(), ".kyanit-builder")

if not os.path.exists(WORK_DIR):
    os.makedirs(WORK_DIR)


class Progress:
    def __init__(self):
        self.val = 0

    def clear(self):
        self.val = 0
        return "     "

    def tick(self):
        self.val += 1
        return ["[-  ]", "[*- ]", "[-*-]", "[ -*]", "[  -]", "[ -*]", "[-*-]", "[*- ]"][
            self.val % 8
        ]


def print_status(proc_name, message, error=False, check_file_path=None, end="\n"):
    print("kyanit-builder: ", end="")
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


def remove_dir_tree(path):
    def handle_remove_ro(func, path, exc):
        exc_value = exc[1]
        if func in (os.rmdir, os.remove, os.unlink) and exc_value.errno == errno.EACCES:
            os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)  # 0777
            func(path)
        else:
            raise exc

    shutil.rmtree(path, onerror=handle_remove_ro)


def git_clone_and_checkout(url, rev, recursive=False):
    # TODO: GIT: Do some progress feedback
    folder_name = url.rpartition("/")[2]
    try:
        print_status("git", f"cloning into '{url}' ...")
        subprocess.Popen(
            ["git", "clone", url],
            cwd=WORK_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).wait()
    except subprocess.CalledProcessError:
        print_status("git", f"cannot clone repository '{url}'.", error=True)
        return False
    else:
        if folder_name is None:
            print_status("git", f"cannot find cloned repository '{url}'.", error=True)
            return False
    try:
        print_status("git", f"checking out rev '{rev}' ...")
        subprocess.Popen(
            ["git", "checkout", rev],
            cwd=os.path.join(WORK_DIR, folder_name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).wait()
    except subprocess.CalledProcessError:
        print_status(
            "git", f"cannot check out rev '{rev}' in '{folder_name}'.", error=True
        )
        return False
    if recursive:
        try:
            print_status("git", "updating submodules (if any) ...")
            subprocess.Popen(
                ["git", "submodule", "update", "--init"],
                cwd=os.path.join(WORK_DIR, folder_name),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).wait()
            return True
        except subprocess.CalledProcessError:
            print_status(
                "git", f"cannot update submodules in '{folder_name}'.", error=True
            )
            return False
    else:
        return True


def build_esp_open_sdk(force_rebuild=False):
    if force_rebuild:
        if os.path.exists(os.path.join(WORK_DIR, "esp-open-sdk")):
            print_status("esp-open-sdk", "removing existing build ...")
            remove_dir_tree(os.path.join(WORK_DIR, "esp-open-sdk"))
        if os.path.exists(os.path.join(WORK_DIR, "esp-open-sdk-build.done")):
            os.remove(os.path.join(WORK_DIR, "esp-open-sdk-build.done"))
    if not os.path.exists(os.path.join(WORK_DIR, "esp-open-sdk")):
        if not git_clone_and_checkout(
            ESP_OPEN_SDK_URL, ESP_OPEN_SDK_REV, recursive=True
        ):
            exit()
    if (
        not os.path.exists(os.path.join(WORK_DIR, "esp-open-sdk-build.done"))
        or force_rebuild
    ):
        try:
            proc = subprocess.Popen(
                "make",
                cwd=os.path.join(WORK_DIR, "esp-open-sdk"),
                shell=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            with open(os.path.join(WORK_DIR, "esp-open-sdk-build.log"), "w") as f:
                p = Progress()
                for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):
                    print_status("esp-open-sdk", f"building ... {p.tick()}", end="\r")
                    f.write(line)
                print_status("esp-open-sdk", f"building ... {p.clear()}")
        except subprocess.CalledProcessError:
            print_status(
                "esp-open-sdk",
                "cannot build.",
                error=True,
                check_file_path=os.path.join(WORK_DIR, "esp-open-sdk-build.log"),
            )
            exit()
        else:
            # check output (could also check for xtensa binary)
            if (
                "Xtensa toolchain is built"
                in open(os.path.join(WORK_DIR, "esp-open-sdk-build.log")).read()
            ):
                with open(os.path.join(WORK_DIR, "esp-open-sdk-build.done"), "w"):
                    pass
                print_status("esp-open-sdk", "done building.")
            else:
                print_status(
                    "esp-open-sdk",
                    "cannot build.",
                    error=True,
                    check_file_path=os.path.join(WORK_DIR, "esp-open-sdk-build.log"),
                )
                exit()


def build_mpy(force_rebuild=False):
    if force_rebuild:
        if os.path.exists(os.path.join(WORK_DIR, "micropython")):
            print_status("micropython", "removing existing build ...")
            remove_dir_tree(os.path.join(WORK_DIR, "micropython"))

    if not os.path.exists(os.path.join(WORK_DIR, "micropython")):
        if not git_clone_and_checkout(MICROPYTHON_URL, MICROPYTHON_REV):
            exit()

    # BUILD MPY-CROSS

    if (
        not os.path.exists(os.path.join(WORK_DIR, "mpy-cross-build.done"))
        or force_rebuild
    ):
        if os.path.exists(os.path.join(WORK_DIR, "mpy-cross-build.done")):
            os.remove(os.path.join(WORK_DIR, "mpy-cross-build.done"))
        try:
            proc = subprocess.Popen(
                "make",
                cwd=os.path.join(WORK_DIR, "micropython", "mpy-cross"),
                shell=True,
                stderr=subprocess.STDOUT,
                stdout=subprocess.PIPE,
            )
            with open(os.path.join(WORK_DIR, "mpy-cross-build.log"), "w") as f:
                p = Progress()
                for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):
                    print_status(
                        "micropython", f"building mpy-cross ... {p.tick()}", end="\r"
                    )
                    f.write(line)
                print_status("micropython", f"building mpy-cross ... {p.clear()}")
        except subprocess.CalledProcessError:
            print_status(
                "micropython",
                "cannot build mpy-cross.",
                error=True,
                check_file_path=os.path.join(WORK_DIR, "mpy-cross-build.log"),
            )
            exit()
        else:
            # check mpy-cross binary exists
            if os.path.exists(
                os.path.join(WORK_DIR, "micropython", "mpy-cross", "mpy-cross")
            ):
                with open(os.path.join(WORK_DIR, "mpy-cross-build.done"), "w"):
                    pass
                print_status("micropython", "done building mpy-cross.")
            else:
                print_status(
                    "micropython",
                    "cannot build mpy-cross.",
                    error=True,
                    check_file_path=os.path.join(WORK_DIR, "mpy-cross-build.log"),
                )
                exit()

    # BUILD SUBMODULES

    if (
        not os.path.exists(os.path.join(WORK_DIR, "mpy-submodules-build.done"))
        or force_rebuild
    ):
        if os.path.exists(os.path.join(WORK_DIR, "mpy-submodules-build.done")):
            os.remove(os.path.join(WORK_DIR, "mpy-submodules-build.done"))
        try:
            custom_env = os.environ.copy()
            custom_env["PATH"] = (
                os.path.join(WORK_DIR, "esp-open-sdk", "xtensa-lx106-elf", "bin")
                + ":"
                + custom_env["PATH"]
            )
            tries = 0
            while True:
                tries += 1
                proc = subprocess.Popen(
                    "make submodules",
                    cwd=os.path.join(WORK_DIR, "micropython", "ports", "esp8266"),
                    shell=True,
                    env=custom_env,
                    stderr=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                )
                with open(os.path.join(WORK_DIR, "mpy-submodules-build.log"), "w") as f:
                    p = Progress()
                    for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):
                        if tries == 1:
                            print_status(
                                "micropython",
                                f"building esp8266 submodules ... {p.tick()}",
                                end="\r",
                            )
                        f.write(line)
                    if tries == 1:
                        print_status(
                            "micropython",
                            f"building esp8266 submodules ... {p.clear()}",
                        )
                    proc_output = proc.communicate()
                    f.write(proc_output[0].decode())
                    f.write(proc_output[1].decode())
                if tries == 2 or not proc_output[1]:
                    break
        except subprocess.CalledProcessError:
            print_status(
                "micropython",
                "cannot build esp8266 submodules.",
                error=True,
                check_file_path=os.path.join(WORK_DIR, "mpy-submodules-build.log"),
            )
            exit()
        else:
            if proc_output[1]:
                print_status(
                    "micropython",
                    "cannot build esp8266 submodules.",
                    error=True,
                    check_file_path=os.path.join(WORK_DIR, "mpy-submodules-build.log"),
                )
                exit()
            else:
                with open(os.path.join(WORK_DIR, "mpy-submodules-build.done"), "w"):
                    pass
                print_status("micropython", "done building esp8266 submodules.")


def configure_mpy(version):
    print_status("configure", "creating board configuration ...")

    # CREATE BOARD DIRECTORY
    try:
        # fmt: off
        # remove possibly existing board configuration
        if os.path.exists(
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT"
            )
        ):
            remove_dir_tree(
                os.path.join(
                    WORK_DIR, "micropython", "ports", "esp8266",
                    "boards", "KYANIT"
                )
            )

        # create new board configuration
        shutil.copytree(
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "GENERIC"
            ),
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT"
            ),
        )
        shutil.copytree(
            os.path.join(WORK_DIR, "micropython", "ports", "esp8266", "modules"),
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT", "modules",
            ),
        )
        shutil.copytree(
            os.path.join(os.getcwd(), "src"),
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT", "modules",
            ),
            dirs_exist_ok=True,
        )
        shutil.copy2(
            os.path.join(os.getcwd(), "mpbuild", "manifest.py"),
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT", "manifest.py",
            ),
        )
        os.remove(
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT", "modules", "inisetup.py",
            )
        )
        # create version file
        with open(
            os.path.join(
                WORK_DIR, "micropython", "ports", "esp8266",
                "boards", "KYANIT", "modules", "kyanit", "_version.py",
            ),
            "w",
        ) as f:
            f.write(f'__version__ = "{version}"\n')
        # fmt: on
    except Exception as e:
        print_status("configure", f"configuration failed with '{e}'.", error=True)
        exit()
    else:
        print_status("configure", "board configuration created.")


def build_kyanit_core():
    if not os.path.exists(os.path.join(os.getcwd(), "src", "kyanit")):
        print_status("build", "current directory is not kyanit core repo.", error=True)
        exit()

    # DETERMINE VERSION NUMBER
    version = GitReleaseStatus().head
    try:
        version_info = semver.VersionInfo.parse(version)
    except (TypeError, ValueError):
        print_status("build", f"version '{version}' is not valid semver.", error=True)
        exit()
    else:
        if version_info.build is None:
            print_status("build", f"building release version '{version}'")
        else:
            print_status("build", f"building development version '{version}'")

    # CONFIGURE
    configure_mpy(version)

    # BUILD FIRMWARE
    try:
        if os.path.exists(os.path.join(WORK_DIR, "kyanit-build.done")):
            os.remove(os.path.join(WORK_DIR, "kyanit-build.done"))
        if os.path.exists(
            os.path.join(WORK_DIR, "micropython", "ports", "esp8266", "build-KYANIT")
        ):
            print_status("build", "removing previous build ...")
            remove_dir_tree(
                os.path.join(
                    WORK_DIR, "micropython", "ports", "esp8266", "build-KYANIT"
                )
            )
        custom_env = os.environ.copy()
        custom_env["PATH"] = (
            os.path.join(WORK_DIR, "esp-open-sdk", "xtensa-lx106-elf", "bin")
            + ":"
            + custom_env["PATH"]
        )
        proc = subprocess.Popen(
            "make BOARD=KYANIT",
            cwd=os.path.join(WORK_DIR, "micropython", "ports", "esp8266"),
            shell=True,
            env=custom_env,
            stderr=subprocess.STDOUT,
            stdout=subprocess.PIPE,
        )
        with open(os.path.join(WORK_DIR, "kyanit-build.log"), "w") as f:
            p = Progress()
            for line in io.TextIOWrapper(proc.stdout, encoding="utf-8"):
                print_status("build", f"building firmware ... {p.tick()}", end="\r")
                f.write(line)
            print_status("build", f"building firmware ... {p.clear()}")
    except subprocess.CalledProcessError:
        print_status(
            "build",
            "cannot build firmware.",
            error=True,
            check_file_path=os.path.join(WORK_DIR, "kyanit-build.log"),
        )
        exit()
    else:
        if not os.path.exists(
            os.path.join(
                WORK_DIR,
                "micropython",
                "ports",
                "esp8266",
                "build-KYANIT",
                "firmware-combined.bin",
            )
        ):
            print_status(
                "build",
                "cannot build firmware.",
                error=True,
                check_file_path=os.path.join(WORK_DIR, "kyanit-build.log"),
            )
            exit()
        else:
            with open(os.path.join(WORK_DIR, "kyanit-build.done"), "w") as f:
                f.write(version)
            print_status("build", "done building firmware.")


def get_fw_binary():
    fw_path = os.path.join(
        WORK_DIR,
        "micropython",
        "ports",
        "esp8266",
        "build-KYANIT",
        "firmware-combined.bin",
    )
    if os.path.exists(fw_path):
        return fw_path
    else:
        return None


def get_fw_version():
    if os.path.exists(os.path.join(WORK_DIR, "kyanit-build.done")):
        with open(os.path.join(WORK_DIR, "kyanit-build.done")) as f:
            ver = f.read()
            try:
                semver.VersionInfo.parse(ver)
            except Exception:
                return None
            else:
                return ver


def fw_upload(serial_port, no_erase=False):
    # TODO: Upload: Catch errors from esptool.py
    fw_ver = get_fw_version()
    fw_path = get_fw_binary()
    if fw_ver is None or fw_path is None:
        print_status("upload", "no existing firmware build found.")
        return

    print_status("upload", f"firmware version is '{fw_ver}'")

    if not no_erase:
        print_status("upload", "erasing flash ...", end="\n\n")
        try:
            subprocess.Popen(
                ["esptool.py", "--port", serial_port, "erase_flash"]
            ).wait()
            print()
        except subprocess.CalledProcessError as e:
            print_status("upload", f"error '{e}' occurred during erase.", error=True)

    print_status("upload", "uploading ...", end="\n\n")
    try:
        subprocess.Popen(
            [
                "esptool.py",
                "--port",
                serial_port,
                "--baud",
                "230400",
                "write_flash",
                "--flash_size=detect",
                "0",
                fw_path,
            ]
        ).wait()
        print()
    except subprocess.CalledProcessError as e:
        print_status("upload", f"error '{e}' occurred during upload.", error=True)
    else:
        print_status("upload", "upload done.")


def command_line():
    parser = argparse.ArgumentParser(
        prog="kyanit-builder",
        description="Command-line application for automating the build of Kyanit Core. "
        "This application is Linux-only, if on Windows 10, use it in WSL.",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="initialize the build environment by building esp-open-sdk and "
        "micropython; this is optional, as it's done automatically with the first "
        "firmware build",
    )
    parser.add_argument(
        "-b",
        "--build",
        action="store_true",
        help="determine version number and build the kyanit core firmware",
    )
    parser.add_argument(
        "-u",
        "--upload",
        metavar="SERIAL_PORT",
        help="upload the firmware to kyanit; by default the previously built firmware "
        "is uploaded; if '--file' is provided, that file is uploaded instead",
    )
    parser.add_argument(
        "--no-erase",
        action="store_true",
        help="do not erase flash before uploading the firmware",
    )
    parser.add_argument("-f", "--file", help="external firmware file to upload")
    parser.add_argument(
        "-v",
        "--firmware-version",
        action="store_true",
        help="print the previously built firmware version (if exists)",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="DIRECTORY",
        help="optional directory where the previously built kyanit core firmware will "
        "be copied",
    )
    parser.add_argument(
        "--rebuild-esp-open-sdk",
        action="store_true",
        help="force the rebuild of esp-open-sdk",
    )
    parser.add_argument(
        "--rebuild-micropython",
        action="store_true",
        help="force the rebuild of micropython",
    )
    parser.add_argument(
        "--rebuild-toolchain",
        action="store_true",
        help="force the rebuild of both esp-open-sdk and micropython",
    )
    args = parser.parse_args()

    try:
        subprocess.Popen("git", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print_status(
            "git",
            "git required, but not found on the system, install git "
            "before using fwtools (see https://git-scm.com/).",
            error=True,
        )

    nothing_to_do = True

    if args.init:
        nothing_to_do = False
        build_esp_open_sdk()
        build_mpy()

    if args.firmware_version:
        nothing_to_do = False
        version = get_fw_version()
        if version is not None:
            print_status("version", f"existing firmware build version is '{version}'.")
        else:
            print_status("version", "no existing firmware build found.", error=True)

    if args.rebuild_esp_open_sdk or args.rebuild_toolchain:
        nothing_to_do = False
        build_esp_open_sdk(force_rebuild=True)

    if args.rebuild_micropython or args.rebuild_toolchain:
        nothing_to_do = False
        build_mpy(force_rebuild=True)

    if args.build:
        nothing_to_do = False
        build_esp_open_sdk()
        build_mpy()
        build_kyanit_core()

    if args.upload:
        nothing_to_do = False
        fw_upload(args.upload, args.no_erase)

    if args.output:
        nothing_to_do = False
        version = get_fw_version()
        if version is not None:
            print_status("export", f"existing firmware build version is '{version}'.")
            try:
                destination = os.path.join(
                    args.output, f"kyanit-firmware-v{version}.bin"
                )
                do_copy = True
                if os.path.exists(destination):
                    print_status(
                        "export", f"'{destination}' exists. overwrite? (Y/n): ", end=""
                    )
                    try:
                        answer = input()
                    except KeyboardInterrupt:
                        print()
                        answer = "N"
                    do_copy = (
                        True if not answer or answer.upper() in ["Y", "YES"] else False
                    )
                if do_copy:
                    shutil.copy2(get_fw_binary(), destination)
                    print_status("export", f"firmware exported to '{destination}'.")
                else:
                    print_status("export", "aborted.")
            except FileNotFoundError:
                print_status("export", f"'{args.output}' not found.", error=True)
            except NotADirectoryError:
                print_status(
                    "export", f"'{args.output}' is not a directory.", error=True
                )
        else:
            print_status("export", "no existing firmware build found.")

    if nothing_to_do:
        parser.print_usage()
