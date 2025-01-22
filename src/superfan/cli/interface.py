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
            with open(default_config) as src, open(config_path, "w") as dst:
                dst.write(src.read())
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
                    
                    # Current RPM
                    readings = self.manager.commander.get_sensor_readings()
                    zone_fans = [r for r in readings if r["name"].startswith("FAN") and 
                               ((zone == "chassis" and not r["name"].startswith("FANA")) or
                                (zone == "cpu" and r["name"].startswith("FANA")))]
                    if zone_fans:
                        rpm_values = [r["value"] for r in zone_fans if r["value"] is not None]
                        if rpm_values:
                            avg_rpm = sum(rpm_values) / len(rpm_values)
                            # Calculate actual percentage based on RPM range
                            if zone == "cpu":
                                min_rpm = 2500  # CPU fan minimum RPM
                                max_rpm = 3800  # CPU fan maximum RPM
                            else:
                                min_rpm = 1000  # Chassis fan minimum RPM
                                max_rpm = 2000  # Chassis fan maximum RPM
                            
                            # Calculate percentage within the valid RPM range
                            if avg_rpm <= min_rpm:
                                actual_pct = 0
                            elif avg_rpm >= max_rpm:
                                actual_pct = 100
                            else:
                                actual_pct = int((avg_rpm - min_rpm) / (max_rpm - min_rpm) * 100)
                            
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
                        if any(pattern.replace('*', '') in sensor for pattern in zone_config["sensors"]):
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
            
            if args.learn:
                # Start learning mode
                print("Starting fan speed learning mode...")
                self.manager.start()
                print("Learning complete. Updated configuration saved.")
                self.manager.stop()
                
            elif args.manual is not None:
                # Set manual fan speed
                self.manager.commander.set_manual_mode()
                self.manager.commander.set_fan_speed(args.manual)
                print(f"Fan speed set to {args.manual}%")
                
            elif args.monitor:
                # Start control loop
                self.manager.start()
                
                # Setup signal handler
                def signal_handler(signum, frame):
                    self._running = False
                signal.signal(signal.SIGINT, signal_handler)
                
                # Initialize curses
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                stdscr.keypad(True)
                
                try:
                    # Run monitor display
                    self._running = True
                    self._monitor_display(stdscr)
                finally:
                    # Clean up curses
                    curses.nocbreak()
                    stdscr.keypad(False)
                    curses.echo()
                    curses.endwin()
                
                # Stop control loop
                self.manager.stop()
                
            else:
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

def main() -> None:
    """Main entry point"""
    cli = CLI()
    cli.run()

if __name__ == "__main__":
    main()
