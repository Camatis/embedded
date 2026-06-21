def flip_mango_for_second_scan():
    """
    Run conveyor for 0.3 seconds to flip mango for second scan.
    Mango stays in chamber.
    """
    print("▶️  RUNNING CONVEYOR FOR 0.3 SECONDS TO FLIP MANGO...")
    set_conveyor_speed(CONVEYOR_SPEED)
    time.sleep(0.3)  # Changed from 0.5 to 0.3 seconds
    print("🛑 STOPPING CONVEYOR...")
    set_conveyor_speed(0)