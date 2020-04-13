import os
import re
import sys
import pdoc
import shutil
import argparse


def clean(docs_dir, toplevel_name):
    shutil.rmtree(os.path.join(docs_dir, toplevel_name), ignore_errors=True)


def load_toplevel(toplevel_name):
    context = pdoc.Context()
    toplevel = pdoc.Module(toplevel_name, context=context)
    pdoc.link_inheritance(context)
    return toplevel


def recurse_modules(mod):
    yield mod
    for submod in mod.submodules():
        yield from recurse_modules(submod)


def module_path(docs_dir, mod, ext):
    return os.path.join(docs_dir, *re.sub(r'\.html$', ext, mod.url()).split('/'))


def touch(filepath):
    try:
        os.makedirs(os.path.dirname(filepath))
    except FileExistsError:
        pass
    with open(filepath, 'w'):
        pass


_excludes = []


def exclude_filter(obj):
    match = re.search(r'\s\'(.*)\'\>$', str(obj))
    if match is not None:
        name = match.group(1)
        if name not in _excludes:
            return True
        else:
            return False
    return True


def generate_htmls(docs_dir, toplevel_name, show_source_code=True):
    toplevel = load_toplevel(toplevel_name)
    for module in recurse_modules(toplevel):
        module_file = module_path(docs_dir, module, '.html')
        touch(module_file)
        with open(module_file, 'w') as file:
            html = pdoc.html(
                module.name,
                docfilter=exclude_filter,
                show_source_code=show_source_code
            )
            file.write(html.replace("…", ""))  # remove ellipses from HTML


def command_line():
    global _excludes

    parser = argparse.ArgumentParser(
        prog="gendocs",
        description="Kyanit Build Tools - gendocs: Documentation generator for Kyanit "
                    "components",
        usage="python -m kyanit_buildtools.%(prog)s TOPLEVEL DOCS_DIR [options...]"
    )

    parser.add_argument(
        "toplevel",
        metavar="TOPLEVEL",
        help="top-level package or module name to create documentation for; gendocs "
             "will recurse to sub-modules",
    )

    parser.add_argument(
        "docs_dir",
        metavar="DOCS_DIR",
        help="output directory for documentation files",
    )

    parser.add_argument(
        "--pythonpath", "-p",
        action="extend",
        nargs="+",
        metavar="DIR",
        help="directories to add to PYTHONPATH before attempting to import the "
             "top-level package or module",
    )

    parser.add_argument(
        "--exclude", "-e",
        action="extend",
        nargs="+",
        metavar="NAME",
        help="object to exclude from documentation generation; ex. mymodule.myfunc",
    )

    parser.add_argument(
        "--with-source", "-s",
        action="store_true",
        help="include source codes in documentation",
    )

    args = parser.parse_args()

    if args.exclude:
        _excludes = args.exclude

    if args.pythonpath:
        for path in args.pythonpath:
            sys.path.append(os.path.join(os.getcwd(), path))

    pdoc.tpl_lookup.directories.insert(0, os.path.join(args.docs_dir, "templates"))

    generate_htmls(args.docs_dir, args.toplevel, args.with_source)
