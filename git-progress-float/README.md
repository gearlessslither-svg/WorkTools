# Git Progress Float

Small macOS floating widget for `git pull`, `git push`, `git fetch`, and `git clone`.

It installs a reversible `git` wrapper at `~/.local/bin/git`. The wrapper delegates to the real `/usr/bin/git`, tees Git stderr back to your terminal, parses progress lines, and writes local task status files. A Swift/AppKit LaunchAgent shows the status as a small always-on-top progress widget.

## Install

```bash
./install_git_progress.sh
```

Then open a new terminal, or run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The installer writes that PATH preference to both `~/.zshrc` and `~/.zprofile`, so interactive terminals and login shells used by command-line AI agents can both find the wrapper.

## Use

Run Git normally:

```bash
git pull
git push
git fetch
git clone https://example.com/repo.git
```

Only network/progress commands are monitored. Other commands such as `git status`, `git diff`, and `git log` are passed straight through to the real Git.

Background scripts and command-line AI agents are shown when they call `git` through PATH with `~/.local/bin` first. Programs that hardcode `/usr/bin/git` bypass the wrapper and will not show widget progress.

## Concurrent Git Jobs

Each monitored Git process writes its own status file under:

```text
~/Library/Application Support/GitProgressFloat/results/tasks/
```

The floating widget aggregates active and recently finished tasks, shows up to 3 rows at once, and keeps a count in the header when more jobs are running.

## Uninstall

```bash
./uninstall_git_progress.sh
```

The widget stores only local status in:

```text
~/Library/Application Support/GitProgressFloat/results/git_progress_status.json
~/Library/Application Support/GitProgressFloat/results/tasks/
```

It avoids storing full remote URLs or command arguments, so tokens embedded in Git remotes are not written to the widget status file.
