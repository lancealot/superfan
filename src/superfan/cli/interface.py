"""
Command Line Interface Module

This module provides the command-line interface for controlling
and monitoring fan speeds.
"""

import argparse
import logging
import os
import signal
import sys
import time
import re
from typing import Optional
import yaml
import curses
from pathlib import Path

from ..control import ControlManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Set default log levels for superfan modules
for name in ['superfan.ipmi.commander', 'superfan.ipmi.sensors', 'superfan.control.manager']:
    logging.getLogger(name).setLevel(logging.INFO)

class CLI:
    """Command-line interface handler"""
    
    def __init__(self):
        """Initialize CLI handler"""
        self.parser = self._create_parser()
        self.manager: Optional[ControlManager] = None
        self._running = False
        self.pid_file = "/var/run/superfan.pid"

    def _check_running(self) -> bool:
        """Check if another instance is running
        
        Returns:
            True if another instance is running
        """
        if os.path.exists(self.pid_file):
            try:
                with open(self.pid_file) as f:
                    pid = int(f.read())
                # Check if process is running
                os.kill(pid, 0)
                return True
            except (OSError, ValueError):
                # Process not running or invalid PID
                os.remove(self.pid_file)
        return False

    def _create_pid_file(self) -> None:
        """Create PID file"""
        with open(self.pid_file, "w") as f:
            f.write(str(os.getpid()))

    def _remove_pid_file(self) -> None:
        """Remove PID file"""
        if os.path.exists(self.pid_file):
            os.remove(self.pid_file)

    def _stop_service(self) -> None:
        """Stop systemd service if running"""
        try:
            os.system("systemctl stop superfan")
            time.sleep(2)  # Wait for service to stop
        except Exception as e:
            logger.warning(f"Failed to stop superfan service: {e}")

    def _create_parser(self) -> argparse.ArgumentParser:
        """Create command-line argument parser
        
        Returns:
            Configured argument parser
        """
        parser = argparse.ArgumentParser(
            description="Superfan - Intelligent Supermicro server fan control"
        )
        
        parser.add_argument(
            "-c", "--config",
            help="Path to configuration file",
            default="/etc/superfan/config.yaml"
        )
        
        parser.add_argument(
            "--monitor",
            action="store_true",
            help="Monitor temperatures and fan speeds",
        )

        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug logging in monitor mode"
        )

        parser.add_argument(
            "--manual",
            type=int,
            choices=range(0, 101),
            metavar="SPEED",
            help="Set manual fan speed (0-100)"
        )
        
        parser.add_argument(
            "--learn",
            action="store_true",
            help="Learn minimum stable fan speeds"
        )
        
        return parser

    def _get_zone_temperature(self, zone_name: str) -> Optional[float]:
        """Get temperature delta for a zone
        
        Args:
            zone_name: Name of the zone
            
        Returns:
            Temperature delta above target, or None if no valid reading
        """
        zone_config = self.manager.config["fans"]["zones"][zone_name]
        base_sensors = zone_config["sensors"]
        highest_temp = None
        
        for base_sensor in base_sensors:
            pattern = base_sensor.replace('*', '.*')
            pattern_re = re.compile(pattern, re.IGNORECASE)
            
            for sensor_name in self.manager.sensor_manager.get_sensor_names():
                if pattern_re.search(sensor_name):
                    stats = self.manager.sensor_manager.get_sensor_stats(sensor_name)
                    if stats:
                        temp = stats["current"]
                        if highest_temp is None or temp > highest_temp:
                            highest_temp = temp
                            
        if highest_temp is None:
            return None
            
        target_temp = zone_config["target"]
        return max(0, highest_temp - target_temp)

    def _setup_config(self, config_path: str) -> str:
        """Setup configuration file
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Path to active configuration file
        """
        # If config doesn't exist, copy default
        if not os.path.exists(config_path):
            default_config = Path(__file__).parent.parent.parent / "config" / "default.yaml"
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            if default_config.exists():
                with open(default_config) as src, open(config_path, "w") as dst:
                    dst.write(src.read())
            else:
                # If default config doesn't exist, create a basic one
                with open(config_path, "w") as f:
                    yaml.safe_dump({
                        "ipmi": {},
                        "temperature": {"hysteresis": 3},
                        "fans": {
                            "polling_interval": 30,
                            "monitor_interval": 5,
                            "min_speed": 5,
                            "max_speed": 100,
                            "ramp_step": 5,
                            "zones": {
                                "chassis": {
                                    "enabled": True,
                                    "critical_max": 75,
                                    "warning_max": 65,
                                    "target": 55,
                                    "sensors": ["System Temp", "Peripheral Temp", "NVMe_*"],
                                    "curve": [[0, 5], [10, 30], [20, 50]]
                                },
                                "cpu": {
                                    "enabled": True,
                                    "critical_max": 85,
                                    "warning_max": 75,
                                    "target": 65,
                                    "sensors": ["CPU1 Temp", "CPU2 Temp"],
                                    "curve": [[0, 20], [10, 30], [20, 50]]
                                }
                            }
                        },
                        "safety": {
                            "watchdog_timeout": 90,
                            "min_temp_readings": 2,
                            "min_working_fans": 2,
                            "restore_on_exit": True
                        }
                    }, f)
            logger.info(f"Created default configuration at {config_path}")
            
        return config_path

    def _monitor_display(self, stdscr) -> None:
        """Display real-time monitoring information
        
        Args:
            stdscr: Curses window object
        """
        # Setup colors
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
        
        # Hide cursor
        curses.curs_set(0)
        
        # Get terminal size
        max_y, max_x = stdscr.getmaxyx()
        
        # Create main window
        win = curses.newwin(max_y, max_x, 0, 0)
        win.keypad(True)
        
        while self._running:
            try:
                # Get current status
                status = self.manager.get_status()
                
                # Clear window
                win.clear()
                
                # Display header
                win.addstr(0, 0, "Superfan Monitor", curses.A_BOLD)
                win.addstr(1, 0, "=" * 70)  # Increased width for new columns
                
                # Display status
                row = 2
                win.addstr(row, 0, "Status: ")
                if status["emergency"]:
                    win.addstr("EMERGENCY", curses.color_pair(3) | curses.A_BOLD)
                else:
                    win.addstr("Normal", curses.color_pair(1))

                # Display fan speeds at the top
                row += 2
                win.addstr(row, 0, "Fan Speeds:", curses.A_BOLD)
                win.addstr(row, 25, "Target Speed", curses.A_BOLD)  # New column
                win.addstr(row, 45, "Current RPM", curses.A_BOLD)   # New column
                for zone, speeds in status["fan_speeds"].items():
                    row += 1
                    # Current speed
                    current_speed = speeds["current"]
                    win.addstr(row, 2, f"{zone}: {current_speed}%")
                    
                    # Target speed
                    target_speed = speeds["target"]
                    if target_speed is not None:
                        win.addstr(row, 25, f"{target_speed}%")
                    elif "target" in speeds:  # Ensure we show target even if None
                        win.addstr(row, 25, f"{speeds['current']}%")
                    
                    # Current RPM
                    readings = self.manager.commander.get_sensor_readings()
                    zone_fans = [r for r in readings if r["name"].startswith("FAN") and 
                               ((zone == "chassis" and not r["name"].startswith("FANA")) or
                                (zone == "cpu" and r["name"].startswith("FANA")))]
                    if zone_fans:
                        rpm_values = [r["value"] for r in zone_fans if r["value"] is not None]
                        if rpm_values:
                            avg_rpm = sum(rpm_values) / len(rpm_values)
                            # Get board configuration
                            board_config = self.manager.config["fans"]["board_config"]
                            speed_steps = board_config["speed_steps"]
                            
                            # Map RPM to next highest step
                            actual_pct = None
                            for step_name, step_info in reversed(speed_steps.items()):
                                rpm_range = step_info["rpm_ranges"][zone]
                                min_rpm = rpm_range["min"] * 0.8  # Allow 20% below minimum
                                if avg_rpm >= min_rpm:
                                    actual_pct = step_info["threshold"]
                                    break
                                    
                            if actual_pct is None:
                                # If no step matches, use medium step for safety
                                actual_pct = speed_steps["medium"]["threshold"]
                            
                            # Update current speed if it differs significantly
                            if abs(actual_pct - current_speed) > 10:
                                logger.warning(f"{zone} fan speed mismatch - Command: {current_speed}%, Actual: {actual_pct}%")
                            win.addstr(row, 45, f"{avg_rpm:.0f} RPM ({actual_pct}%)")
                    
                # Display temperatures
                row += 2
                win.addstr(row, 0, "Temperatures:", curses.A_BOLD)
                win.addstr(row, 35, "Target Temp", curses.A_BOLD)  # New column
                for sensor, temp in status["temperatures"].items():
                    row += 1
                    # Current temperature
                    win.addstr(row, 2, f"{sensor}: ")
                    color = curses.color_pair(1)
                    if temp >= 75:
                        color = curses.color_pair(3)
                    elif temp >= 65:
                        color = curses.color_pair(2)
                    win.addstr(f"{temp:.1f}°C", color)
                    
                    # Target temperature
                    for zone_name, zone_config in self.manager.config["fans"]["zones"].items():
                        # CPU zone: Match CPU and VRM sensors
                        if zone_name == "cpu":
                            if "CPU" in sensor or "VRM" in sensor:
                                target = zone_config["target"]
                                win.addstr(row, 35, f"{target}°C")
                                break
                        # Chassis zone: Match everything else
                        else:
                            # Convert sensor patterns to regex for proper matching
                            sensor_patterns = [pattern.replace('*', '.*') for pattern in zone_config["sensors"]]
                            if any(re.search(pattern, sensor, re.IGNORECASE) for pattern in sensor_patterns):
                                target = zone_config["target"]
                                win.addstr(row, 35, f"{target}°C")
                                break
                            
                # Display footer
                row += 2
                win.addstr(row, 0, "=" * 70)  # Match header width
                win.addstr(row + 1, 0, "Press Ctrl+C to exit")
                
                # Refresh window
                win.refresh()
                
                # Wait before next update - use monitor_interval in monitor mode
                interval = self.manager.config["fans"]["monitor_interval"]
                time.sleep(interval)
                
            except curses.error:
                # Handle terminal resize
                max_y, max_x = stdscr.getmaxyx()
                win.resize(max_y, max_x)
                win.clear()
                win.refresh()
                
    def run(self) -> None:
        """Run the CLI interface"""
        args = self.parser.parse_args()
        
        try:
            # Setup configuration
            config_path = self._setup_config(args.config)
            
            # Set debug logging if requested
            if args.debug:
                for name in ['superfan.ipmi.commander', 'superfan.ipmi.sensors', 'superfan.control.manager']:
                    logging.getLogger(name).setLevel(logging.DEBUG)

            # Initialize control manager with mode flags
            self.manager = ControlManager(
                config_path,
                monitor_mode=bool(args.monitor),
                learning_mode=bool(args.learn)
            )
            
            if args.learn or args.monitor:
                # Check for running instance
                if self._check_running():
                    print("Another instance of superfan is running")
                    print("Please stop the service first: sudo systemctl stop superfan")
                    return

                # Stop systemd service
                self._stop_service()

                # Create PID file
                self._create_pid_file()

            if args.learn:
                print("Starting fan speed and temperature response learning...")
                print("\nThis process will:")
                print("1. Test different fan speed steps")
                print("2. Record RPM ranges for each zone")
                print("3. Measure temperature response")
                print("\nThis will take approximately 20-30 minutes.")
                print("The system may get warmer than usual during testing.")
                
                try:
                    input("\nPress Enter to start learning, or Ctrl+C to cancel...")
                except KeyboardInterrupt:
                    print("\nLearning cancelled.")
                    self._remove_pid_file()
                    return
                
                # Start learning mode
                self.manager.start()
                
                print("\nLearning complete!")
                print("\nUpdated configuration includes:")
                print("- Fan speed steps and RPM ranges")
                print("- Temperature response characteristics")
                print("- Safe minimum and maximum speeds")
                print("\nConfiguration saved to:", args.config)
                
                self.manager.stop()
                
            elif args.manual is not None:
                # Set manual fan speed
                self.manager.commander.set_manual_mode()
                self.manager.commander.set_fan_speed(args.manual)
                print(f"Fan speed set to {args.manual}%")
                
            elif args.monitor:
                # Initialize curses first
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                
                try:
                    # Setup signal handler
                    def signal_handler(signum, frame):
                        self._running = False
                    signal.signal(signal.SIGINT, signal_handler)
                    
                    # Enable keypad mode
                    stdscr.keypad(True)
                    
                    # Start control loop
                    self.manager.start()
                    
                    # Run monitor display
                    self._running = True
                    self._monitor_display(stdscr)
                    
                    # Stop control loop
                    self.manager.stop()
                finally:
                    # Clean up curses
                    curses.nocbreak()
                    stdscr.keypad(False)
                    curses.echo()
                    curses.endwin()
                
            else:
                # Setup signal handler
                def signal_handler(signum, frame):
                    pass
                signal.signal(signal.SIGINT, signal_handler)
                
                # Start control loop
                self.manager.start()
                
                print("Control loop started. Press Ctrl+C to exit.")
                
                # Wait for interrupt
                signal.pause()
                
                # Stop control loop
                self.manager.stop()
                
        except KeyboardInterrupt:
            if self.manager:
                self.manager.stop()
            print("\nExiting...")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            sys.exit(1)
            
        finally:
            # Clean up
            self._remove_pid_file()
            
            # Restart service if we stopped it
            if args.learn or args.monitor:
                try:
                    os.system("systemctl start superfan")
                except Exception as e:
                    logger.warning(f"Failed to restart superfan service: {e}")

def main() -> None:
    """Main entry point"""
    cli = CLI()
    cli.run()

if __name__ == "__main__":
    main()
