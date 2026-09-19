---
name: gui-change-verification
description: Run and functionally verify this project's Mac GUI against the real MP157 and MPU6050 after modifying GUI source code, its launcher, or runtime dependency configuration. Use for implementation and bug-fix requests that change `gui/`, `run_gui.sh`, or GUI startup behavior; do not use demo samples or use this skill for read-only inspection.
---

# GUI Change Verification

After every GUI-related modification, run the GUI against the connected MP157 and MPU6050 and verify its main functions with real RPMsg measurements, not merely that its window opens, before reporting the task complete.

Do not use `--demo`, generated samples, injected samples, mocks, or simulated transports as acceptance evidence. They may be used only for internal diagnosis and never satisfy this skill's verification requirement.

## Required verification

1. Run lightweight static checks appropriate to the changed files. At minimum, compile-check changed Python modules and syntax-check changed shell launchers.
2. Confirm that the MP157 is reachable through the configured wired interface, SSH accepts a connection, M4 is running the expected `m4_rpmsg.elf` firmware, `/dev/ttyRPMSG0` exists, and live MPU6050 records can be read. Resolve recoverable project-side problems before launching the GUI.
3. Start the GUI without demo flags through the project launcher:

   ```sh
   ./run_gui.sh
   ```

4. Exercise the main user workflow with real sensor data. At minimum verify that:

   - starting and stopping acquisition changes the controls and status correctly;
   - samples are received and the acceleration, angular-velocity, attitude, and trajectory views refresh without exceptions;
   - clearing data resets plots, metrics, trajectory, and integration state;
   - relevant configuration controls accept changes and affect the next update or acquisition as intended;
   - closing the window stops timers, workers, subprocesses, and SSH sessions cleanly.

   Prefer deterministic Qt interaction or an existing automated test while keeping the real SSH/RPMsg source. Otherwise interact with the visible GUI and inspect its status, console, plots, and sensor response. Test every feature directly affected by the change in addition to this minimum workflow.
5. Treat a zero exit status as necessary but not sufficient. Review stdout/stderr and the GUI console for tracebacks, crashes, Qt aborts, missing imports, frozen controls, empty plots, stale status, dropped connections, and startup failures.
6. If the board, SSH service, RPMsg device, firmware, or sensor is unavailable, report the exact blocker and leave the GUI change unverified. Do not substitute demo data and do not claim completion.
7. If verification fails for a project-side defect, diagnose and fix it, then repeat the complete real-hardware workflow. Do not finish merely because the code was edited or the window opened.

## Completion rule

Only report a GUI modification as complete after a real-hardware GUI run exits or is stopped cleanly and both startup and main-function results have been checked. State which workflows were exercised and summarize the observed live MP157/MPU6050 data. Demo-only verification is never completion.
