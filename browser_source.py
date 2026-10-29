"""Turn pages captured in a real browser into source files the summarizers can read.

Usage: python browser_source.py FILE [FILE ...]
Each FILE is a work/<date>/sources/<story>/<n>-browser.txt capture written by the browser tool
(runbook step 2). Good captures are rewritten in place as plain text; bot-check pages and
near-empty pages are deleted so no summarizer reads them. Exit code 1 when any file was dropped.
A path that is not <this folder>/work/<date>/sources/<story>/<n>-browser.txt is refused (exit code 2) and nothing is touched.
"""
import sys
from pathlib import Path

from ainews import sources, store


def refusal_reason(capture_path, root):
    """None when the path is root/work/<date>/sources/<story>/<n>-browser.txt (symlinks resolved), else why not."""
    try:
        relative_parts = capture_path.resolve().relative_to(root.resolve()).parts
    except (ValueError, OSError):
        return "is outside this folder"
    if len(relative_parts) != 5 or relative_parts[0] != "work" or relative_parts[2] != "sources":
        return "is not inside work/<date>/sources/<story>/"
    if store.DAY_FOLDER_NAME.fullmatch(relative_parts[1]) is None:
        return "is not inside work/<date>/sources/<story>/"
    if not relative_parts[4].endswith("-browser.txt"):
        return "does not end in -browser.txt"
    return None


def convert(capture_paths):
    """Returns [(path, None or the reason it was dropped)] in input order."""
    results = []
    for capture_path in capture_paths:
        try:
            text = sources.browser_capture_text(capture_path.read_text(encoding="utf-8"))
        except (sources.SourceError, OSError, UnicodeDecodeError) as error:
            capture_path.unlink(missing_ok=True)
            results.append((capture_path, str(error)))
            continue
        store.write_text_atomic(capture_path, text + "\n")
        results.append((capture_path, None))
    return results


def main():
    store.use_utf8_console()
    capture_paths = []
    for argument in sys.argv[1:]:
        capture_paths.append(Path(argument))
    if len(capture_paths) == 0:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parent
    refused = False
    for capture_path in capture_paths:
        reason = refusal_reason(capture_path, root)
        if reason is not None:
            print(f"refused: {capture_path}: {reason}")
            refused = True
    if refused:
        return 2
    exit_code = 0
    for capture_path, reason in convert(capture_paths):
        if reason is None:
            print(f"ok: {capture_path}")
        else:
            print(f"dropped: {capture_path}: {reason}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
