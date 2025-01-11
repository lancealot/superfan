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
            help="Monitor temperatures and fan speeds"
        )
        
        parser.add_argument(
            "--manual",
            type=int,
            choices=range(0, 101),
            metavar="SPEED",
            help="Set manual fan speed (0-100)"
        )
        
        return parser

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
        
        while self._running:
            try:
                # Get current status
                status = self.manager.get_status()
                
                # Clear screen
                stdscr.clear()
                
                # Display header
                stdscr.addstr(0, 0, "Superfan Monitor", curses.A_BOLD)
                stdscr.addstr(1, 0, "=" * 50)
                
                # Display status
                row = 2
                stdscr.addstr(row, 0, "Status: ")
                if status["emergency"]:
                    stdscr.addstr("EMERGENCY", curses.color_pair(3) | curses.A_BOLD)
                else:
                    stdscr.addstr("Normal", curses.color_pair(1))
                    
                # Display temperatures
                row += 2
                stdscr.addstr(row, 0, "Temperatures:", curses.A_BOLD)
                for sensor, temp in status["temperatures"].items():
                    row += 1
                    color = curses.color_pair(1)
                    if temp >= 75:
                        color = curses.color_pair(3)
                    elif temp >= 65:
                        color = curses.color_pair(2)
                    stdscr.addstr(row, 2, f"{sensor}: ")
                    stdscr.addstr(f"{temp:.1f}°C", color)
                    
                # Display fan speeds
                row += 2
                stdscr.addstr(row, 0, "Fan Speeds:", curses.A_BOLD)
                for zone, speed in status["fan_speeds"].items():
                    row += 1
                    stdscr.addstr(row, 2, f"{zone}: {speed}%")
                    
                # Display footer
                row += 2
                stdscr.addstr(row, 0, "=" * 50)
                stdscr.addstr(row + 1, 0, "Press Ctrl+C to exit")
                
                # Refresh display
                stdscr.refresh()
                
                # Wait before next update
                time.sleep(1)
                
            except curses.error:
                # Handle terminal resize
                stdscr.clear()
                stdscr.refresh()
                
    def run(self) -> None:
        """Run the CLI interface"""
        args = self.parser.parse_args()
        
        try:
            # Setup configuration
            config_path = self._setup_config(args.config)
            
            # Initialize control manager
            self.manager = ControlManager(config_path)
            
            if args.manual is not None:
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
                
                # Run monitor display
                self._running = True
                curses.wrapper(self._monitor_display)
                
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
