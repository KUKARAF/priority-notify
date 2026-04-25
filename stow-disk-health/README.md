# Disk Health Check — Stow Package

Weekly disk health monitoring that sends critical notifications to [notifications.osmosis.page](https://notifications.osmosis.page) when issues are found.

## Requirements

- `smartmontools` — provides `smartctl`
- `jq` — JSON processing
- `curl` — HTTP requests
- `cron` — scheduling

## What it checks

| Disk type | Checks |
|-----------|--------|
| HDD/SATA SSD | SMART health status, reallocated sectors, pending sectors, uncorrectable errors |
| NVMe | Media errors, wear level (>= 90%), critical warning flags |

Runs every Sunday at 9:00 AM. Only sends a notification if issues are found.

## Setup

### 1. Symlink the script

```bash
cd stow-disk-health/
stow --adopt -t ~ home
```

This places `disk-health-check` at `~/.local/bin/disk-health-check`.

### 2. Configure

```bash
./install.sh
```

This creates `~/.config/disk-health-check/.env` from the template and adds the cron entry. Edit the `.env` file to set your API token:

```bash
$EDITOR ~/.config/disk-health-check/.env
```

### 3. Sudoers rule for smartctl

`smartctl` requires root. Add a passwordless sudo rule so cron can run it:

```bash
sudo visudo -f /etc/sudoers.d/smartctl
```

Add:

```
your-username ALL=(root) NOPASSWD: /usr/sbin/smartctl
```

### 4. Test

```bash
disk-health-check
```

If disks are healthy, it exits silently. To verify it can reach the API, temporarily lower a threshold in the script or check a known-bad disk.
