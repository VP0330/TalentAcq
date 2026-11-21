---
description: Check (and with your OK, install) the resume-screener prerequisites
allowed-tools: Bash(python:*)
---

Check the prerequisites for the resume-screener plugin and help the recruiter fix anything missing. Nothing is skipped or run in a degraded mode: every missing item is reported with its fix.

1. Run:

   ```
   python "${CLAUDE_PLUGIN_ROOT}/scripts/check_prereqs.py"
   ```

   Each line is `OK: ...` or `MISSING: ...`, followed by a `Fix:` line for missing items. Exit code 0 means every required item is present.

   If the `python` command itself is not found, Python is missing: tell the recruiter to install Python 3.9 or newer from https://www.python.org/downloads/ (or `winget install Python.Python.3.12`), reopen Claude Code, and run `/resume-screener:setup` again. Stop there.

2. If **PyMuPDF** is reported missing, show the recruiter the exact `Fix:` command from the output (a `python -m pip install --user -r ".../requirements.txt"` command) and ask whether to run it. Run it only after the recruiter says yes. If they decline, stop and tell them PDF resumes cannot be read until it is installed.

3. If the **.doc converter** is reported missing, explain that old `.doc` Word files cannot be processed until Microsoft Word or LibreOffice is available, and give the fix from the output (save those files as `.docx`, or install LibreOffice). This is only a blocker for roles that contain `.doc` files. Do not install anything for it yourself.

4. If you installed anything, run the check from step 1 again.

5. Report the final result to the recruiter in plain words: what is OK, what is still missing and how to fix it, and whether they are ready to run `/resume-screener:screen <role-folder>`.
