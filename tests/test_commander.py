[Previous content remains the same]

def test_fan_speed_percentage_conversion(mock_subprocess):
    """Test fan speed percentage to hex conversion"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            result = MagicMock()
            result.stdout = "Base Board Information\n\tProduct Name: H12SSL-i"
            result.stderr = ""
            result.returncode = 0
            return result
        elif cmd == ["ipmitool", "mc", "info"]:
            result = MagicMock()
            result.stdout = "Firmware Revision : 3.88"
            result.stderr = ""
            result.returncode = 0
            return result
        elif "raw" in cmd:
            # Store the command for verification
            mock_run_command.last_command = cmd
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    
    # Test standard board conversion (0-100% -> 0x00-0xFF)
    commander.board_gen = MotherboardGeneration.X13
    commander.set_fan_speed(0, zone="chassis")
    assert "0x04" in mock_run_command.last_command  # Minimum 2%
    
    commander.set_fan_speed(50, zone="chassis")
    assert "0x7f" in mock_run_command.last_command  # 50% -> ~0x7F
    
    commander.set_fan_speed(100, zone="chassis")
    assert "0xff" in mock_run_command.last_command  # 100% -> 0xFF
    
    # Test H12 board conversion (direct percentage)
    commander.board_gen = MotherboardGeneration.H12
    commander.set_fan_speed(0, zone="chassis")
    assert "0x14" in mock_run_command.last_command  # Minimum 20%
    
    commander.set_fan_speed(50, zone="chassis")
    assert "0x32" in mock_run_command.last_command  # 50% -> 0x32
    
    commander.set_fan_speed(100, zone="chassis")
    assert "0x64" in mock_run_command.last_command  # 100% -> 0x64

def test_fan_speed_command_construction(mock_subprocess):
    """Test fan speed command construction for different boards"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            result = MagicMock()
            result.stdout = "Base Board Information\n\tProduct Name: H12SSL-i"
            result.stderr = ""
            result.returncode = 0
            return result
        elif cmd == ["ipmitool", "mc", "info"]:
            result = MagicMock()
            result.stdout = "Firmware Revision : 3.88"
            result.stderr = ""
            result.returncode = 0
            return result
        elif "raw" in cmd:
            # Store the command for verification
            mock_run_command.last_command = " ".join(cmd)
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            return result
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    
    # Test X9 command format
    commander.board_gen = MotherboardGeneration.X9
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x91 0x5A 0x3 0x10" in mock_run_command.last_command
    
    # Test X10/X11/X13 command format
    commander.board_gen = MotherboardGeneration.X13
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x70 0x66 0x01 0x00" in mock_run_command.last_command
    
    # Test H12 command format
    commander.board_gen = MotherboardGeneration.H12
    commander.set_fan_speed(50, zone="chassis")
    assert "raw 0x30 0x91 0x5A 0x03 0x10" in mock_run_command.last_command
    
    # Test zone ID selection
    commander.set_fan_speed(50, zone="cpu")
    assert "0x11" in mock_run_command.last_command  # CPU zone
    commander.set_fan_speed(50, zone="chassis")
    assert "0x10" in mock_run_command.last_command  # Chassis zone

def test_board_detection_firmware_version(mock_subprocess):
    """Test board detection via firmware version"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            if mock_run_command.case == "x13":
                return MagicMock(stdout="Firmware Revision : 3.88", stderr="", returncode=0)
            elif mock_run_command.case == "x11":
                return MagicMock(stdout="Firmware Revision : 2.45", stderr="", returncode=0)
            elif mock_run_command.case == "x10":
                return MagicMock(stdout="Firmware Revision : 1.71", stderr="", returncode=0)
    
    # Test X13 detection via firmware 3.x
    mock_run_command.case = "x13"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X13
    
    # Test X11 detection via firmware 2.x
    mock_run_command.case = "x11"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X11
    
    # Test X10 detection via firmware 1.x
    mock_run_command.case = "x10"
    mock_subprocess.side_effect = mock_run_command
    commander = IPMICommander()
    assert commander.board_gen == MotherboardGeneration.X10

def test_board_detection_multiple_methods(mock_subprocess):
    """Test board detection with multiple detection methods"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            # DMI info fails, forcing IPMI detection
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            # Return both board info and firmware version
            return MagicMock(
                stdout=(
                    "Board Info: X13DPH-T\n"
                    "Firmware Revision : 1.71\n"  # X10 firmware, but X13 board info should take precedence
                ),
                stderr="",
                returncode=0
            )
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    # Board info (X13) should take precedence over firmware version (1.71 would suggest X10)
    assert commander.board_gen == MotherboardGeneration.X13

def test_board_detection_fallback(mock_subprocess):
    """Test board detection fallback behavior"""
    def mock_run_command(cmd, *args, **kwargs):
        if cmd == ["sudo", "dmidecode", "-t", "baseboard"]:
            # First attempt: DMI info fails
            raise subprocess.CalledProcessError(1, cmd, stderr="Error")
        elif cmd == ["ipmitool", "mc", "info"]:
            if mock_run_command.attempts == 0:
                # First attempt: IPMI info fails
                mock_run_command.attempts += 1
                raise subprocess.CalledProcessError(1, cmd, stderr="Error")
            else:
                # Second attempt: IPMI info succeeds
                return MagicMock(stdout="Firmware Revision : 3.88", stderr="", returncode=0)
    mock_run_command.attempts = 0
    mock_subprocess.side_effect = mock_run_command
    
    commander = IPMICommander()
    # Should detect X13 via firmware version after retry
    assert commander.board_gen == MotherboardGeneration.X13
    assert mock_run_command.attempts == 1  # Verify retry occurred
