# Git Progress Float

Small macOS floating widget for `git pull`, `git push`, `git fetch`, and `git clone`.

It installs a reversible `git` wrapper at `~/.local/bin/git`. The wrapper delegates to the real `/usr/bin/git`, tees Git stderr back to your terminal, parses progress lines, and writes a local status file. A Swift/AppKit LaunchAgent shows the status as a small always-on-top progress widget.

## Install

```bash
./install_git_progress.sh
```

Then open a new terminal, or run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Use

Run Git normally:

```bash
git pull
git push
git fetch
git clone https://example.com/repo.git
```

Only network/progress commands are monitored. Other commands such as `git status`, `git diff`, and `git log` are passed straight through to the real Git.

Background scripts are shown when they call `git` through PATH with `~/.local/bin` first. Programs that hardcode `/usr/bin/git` bypass the wrapper and will not show widget progress.

## Uninstall

```bash
./uninstall_git_progress.sh
```

The widget stores only local status in:

```text
~/Library/Application Support/GitProgressFloat/results/git_progress_status.json
```

It avoids storing full remote URLs or command arguments, so tokens embedded in Git remotes are not written to the widget status file.
