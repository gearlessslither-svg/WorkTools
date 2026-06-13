on run
	set scriptPath to POSIX path of (path to me)
	set scriptDir to do shell script "/usr/bin/dirname " & quoted form of scriptPath
	set installScript to scriptDir & "/install_panel.sh"
	set panelUrl to "http://127.0.0.1:18790/"
	set shellCommand to "export PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin; if ! /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:18790/api/ping >/dev/null 2>&1; then /bin/chmod +x " & quoted form of installScript & "; " & quoted form of installScript & " >/tmp/mullvad-speed-guard-panel-install.log 2>&1 || true; fi; for i in 1 2 3 4 5 6 7 8 9 10; do if /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:18790/api/ping >/dev/null 2>&1; then /usr/bin/open " & quoted form of panelUrl & "; exit 0; fi; /bin/sleep 0.5; done; /usr/bin/open " & quoted form of panelUrl
	do shell script shellCommand
end run
