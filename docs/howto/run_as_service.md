# Running scanner as a systemd service

This guide describes how to run `python -m scanner run ...` as a systemd service on Ubuntu.

## Install

> Requires root privileges.

```bash
sudo scripts/install_service.sh
```

This script creates the `scanner` system user, installs the unit file, copies example configs
into `/etc/research_scanner/configs`, and sets up `/var/lib/research_scanner/output`.
If your install lives outside `/opt/research_scanner`, set `RESEARCH_SCANNER_HOME` in
`/etc/research_scanner/research-scanner.env` so the unit can find `scripts/run_service.sh`.

## Start an instance

```bash
sudo systemctl start research-scanner@smoke
sudo systemctl status research-scanner@smoke
```

## Logs

```bash
sudo journalctl -u research-scanner@smoke -f
```

## Stop an instance

```bash
sudo systemctl stop research-scanner@smoke
```

## Configure instances

* Configs live in `/etc/research_scanner/configs/<instance>.yaml`.
* Environment overrides are in `/etc/research_scanner/research-scanner.env`.

## Uninstall

```bash
sudo scripts/uninstall_service.sh
```

This removes the unit file but leaves data/configs in place.
