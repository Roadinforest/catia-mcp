"""Portable release entry: select WHUCAD reconstruction explicitly."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('serve', 'validate', 'execute'):
        print('Usage: run.py {serve|validate|execute} [arguments]')
        return 2
    action, args = sys.argv[1], sys.argv[2:]
    env = dict(os.environ)
    env.update(WHUCAD_CONVERTER=str(ROOT / 'vendor/whucad_converter'),
               PYTHONPATH=str(ROOT / 'src'), PYTHONNOUSERSITE='1', PYTHONIOENCODING='utf-8')
    if action == 'serve':
        if any(arg == '--protocol' or arg.startswith('--protocol=') or arg == '--with-script' for arg in args):
            raise SystemExit('Release entry fixes whucad-migration and disallows script bypass.')
        command = ['-m', 'catia_mcp', '--protocol', 'whucad-migration', *args]
    else:
        if not args:
            raise SystemExit('Supply a program JSON path, e.g. examples/00000000.json')
        # Resolve caller paths before starting the child in the release directory.
        args[0] = str(Path(args[0]).resolve())
        for option in ('--output', '--settings-dir'):
            if option in args:
                i = args.index(option) + 1
                if i < len(args):
                    args[i] = str(Path(args[i]).resolve())
        command = [str(ROOT / 'scripts/execute_mcp_json.py'), *args]
        if action == 'validate':
            command.append('--validate-only')
    return subprocess.call([sys.executable, *command], env=env, cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())
