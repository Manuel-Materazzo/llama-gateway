"""Idle Watcher service running on the local GPU node to trigger automatic poweroff after inactivity."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from typing import Optional
import httpx
import yaml

from gateway.config import IdleWatcherConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [idle_watcher] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("idle_watcher")


def get_system_uptime_seconds() -> Optional[float]:
    """Get system uptime in seconds on Linux/Unix systems."""
    try:
        if os.path.exists("/proc/uptime"):
            with open("/proc/uptime", "r", encoding="utf-8") as f:
                return float(f.readline().split()[0])
    except Exception as e:
        logger.debug("Could not read /proc/uptime: %s", e)
    return None


class IdleWatcher:
    """Monitors gateway activity status and initiates poweroff when threshold is exceeded."""

    def __init__(self, config: IdleWatcherConfig) -> None:
        self.config = config
        self.start_time = time.time()
        self._running = True

    def is_in_grace_period(self) -> tuple[bool, float]:
        """
        Check if the system is currently within the post-boot / post-startup grace period.
        Returns (in_grace_period, seconds_remaining).
        """
        now = time.time()
        uptime = get_system_uptime_seconds()

        # If system uptime is available (Linux), use the lower of system uptime and service runtime
        elapsed = now - self.start_time if uptime is None else min(uptime, now - self.start_time)

        if elapsed < self.config.grace_period:
            return True, self.config.grace_period - elapsed
        return False, 0.0

    def query_gateway_status(self) -> Optional[dict]:
        """Query the Gateway status endpoint. Returns status dict or None if unreachable."""
        url = f"{self.config.gateway_url.rstrip('/')}/gateway/status"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url)
                if response.status_code == 200:
                    return response.json()
                logger.warning("Gateway status endpoint returned HTTP %d: %s", response.status_code, response.text)
                return None
        except httpx.ConnectError:
            logger.warning("Failed to connect to Gateway at %s (connection refused / network down)", url)
            return None
        except httpx.TimeoutException:
            logger.warning("Timeout while connecting to Gateway at %s", url)
            return None
        except Exception as e:
            logger.warning("Unexpected error querying Gateway status at %s: %s", url, e)
            return None

    def execute_shutdown(self) -> None:
        """Executes the system shutdown command."""
        cmd = self.config.shutdown_command
        logger.warning("INACTIVITY THRESHOLD EXCEEDED! Initiating system shutdown command: '%s'", cmd)

        if self.config.dry_run:
            logger.info("[DRY RUN] Shutdown command skipped because dry_run=True.")
            return

        try:
            # Execute command
            subprocess.run(cmd, shell=True, check=True)
        except Exception as e:
            logger.error("Failed to execute shutdown command '%s': %s", cmd, e, exc_info=True)

    def check_and_act(self) -> bool:
        """
        Check idle state once.
        Returns True if shutdown was initiated, False otherwise.
        """
        in_grace, remaining_grace = self.is_in_grace_period()
        if in_grace:
            logger.info("System is currently in grace period (%.1fs remaining). Skipping shutdown checks.", remaining_grace)
            return False

        status = self.query_gateway_status()
        if status is None:
            logger.info("Gateway is currently unreachable. Preserving system state (no shutdown action taken).")
            return False

        inactivity_seconds = status.get("inactivity_seconds")
        waking_in_progress = status.get("waking_in_progress", False)
        last_activity_iso = status.get("last_activity_iso", "unknown")

        logger.info(
            "Gateway report: last activity=%s, idle=%.1fs / %.1fs threshold, waking_in_progress=%s",
            last_activity_iso,
            inactivity_seconds if inactivity_seconds is not None else -1,
            self.config.inactivity_threshold,
            waking_in_progress,
        )

        if waking_in_progress:
            logger.info("Gateway has a wake-up in progress. Resetting idle wait.")
            return False

        if inactivity_seconds is not None and inactivity_seconds >= self.config.inactivity_threshold:
            self.execute_shutdown()
            return True

        return False

    def run(self) -> None:
        """Continuous polling loop."""
        logger.info(
            "Starting Idle Watcher (poll_interval=%.1fs, inactivity_threshold=%.1fs, grace_period=%.1fs, dry_run=%s)",
            self.config.poll_interval,
            self.config.inactivity_threshold,
            self.config.grace_period,
            self.config.dry_run,
        )

        while self._running:
            try:
                shutdown_triggered = self.check_and_act()
                if shutdown_triggered and not self.config.dry_run:
                    logger.info("Shutdown initiated. Exiting watcher loop.")
                    break
            except Exception as e:
                logger.error("Unhandled error in Idle Watcher loop: %s", e, exc_info=True)

            time.sleep(self.config.poll_interval)


def load_watcher_config(config_path: Optional[str] = None) -> IdleWatcherConfig:
    """Load IdleWatcherConfig from YAML file or environment."""
    if not config_path:
        config_path = os.environ.get("WATCHER_CONFIG_PATH", "config.yaml")

    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            watcher_section = data.get("watcher", {})
            return IdleWatcherConfig.model_validate(watcher_section)

    return IdleWatcherConfig()


def main() -> None:
    """CLI Entry point for Idle Watcher."""
    parser = argparse.ArgumentParser(description="Llama Gateway Idle Watcher for local GPU node")
    parser.add_argument("--config", "-c", type=str, help="Path to config.yaml file")
    parser.add_argument("--gateway-url", "-g", type=str, help="Gateway base URL (e.g. http://10.10.0.1:8000)")
    parser.add_argument("--threshold", "-t", type=float, help="Inactivity threshold in seconds")
    parser.add_argument("--grace-period", type=float, help="Grace period post-boot in seconds")
    parser.add_argument("--poll-interval", type=float, help="Poll interval in seconds")
    parser.add_argument("--dry-run", action="store_true", help="Run without actually executing shutdown")

    args = parser.parse_args()
    config = load_watcher_config(args.config)

    if args.gateway_url:
        config.gateway_url = args.gateway_url
    if args.threshold:
        config.inactivity_threshold = args.threshold
    if args.grace_period:
        config.grace_period = args.grace_period
    if args.poll_interval:
        config.poll_interval = args.poll_interval
    if args.dry_run:
        config.dry_run = True

    watcher = IdleWatcher(config)
    watcher.run()


if __name__ == "__main__":
    main()
