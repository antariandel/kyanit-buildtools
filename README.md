# __Kyanit__ Build Tools

This repository contains tools required to build Kyanit's components. It used by CI/CD
scripts and pre-commit hooks.

It contains the following console applications:

```kyanit-buildtools-gendocs``` for automated documentation generation.

```kyanit-buildtools-genrelease``` for version string and changelog generation
(requires git to be installed).

```kyanit-buildtools-fwtools``` for automated build of Kyanit Core firmware on top of
MicroPython.

Access the help of all of these applications by passing ```-h``` to them in the
command line.

## Installation (Ubuntu 20.04)

The following dependencies need to be installed for fwtools to work:

```bash
sudo apt install git wget flex bison gperf python3 python3-pip python3-setuptools \
python3-pyparsing
```

Finally install the build tools with ```pip install kyanit-buildtools```.

The first time ```kyanit-buildtools-fwtools``` is run to build Kyanit Core, the required
toolchain is also built. This takes a fairly long time, but it is only done once.

## License Notice

Copyright (C) 2020 Zsolt Nagy

This program is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, version
3 of the License.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.
See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with this
program. If not, see <https://www.gnu.org/licenses/>.
