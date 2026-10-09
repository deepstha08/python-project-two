# CARLA Multi-Busstop Autonomous Driving Simulation

This project implements a multi-bus-stop scenario in CARLA and Unreal Engine,
where multiple autonomous buses navigate Town05, detect designated bus stop
signboards, park at the correct stop, wait, and continue driving autonomously.

The project was developed as part of an internship and is designed to be
extended into a Master’s thesis in autonomous driving and computer vision.

---

## Key Features

- Multi-bus autonomous driving in CARLA Town05
- Unreal Engine bus stop signboards with unique IDs
- Bus-to-bus-stop matching using ID-based logic
- Overlap-based detection and control
- Autonomous stopping, waiting, and rejoining traffic
- Scalable design for additional stops and vehicles

---

## Scenario Description

- Two bus stops are placed sequentially in Town05
- Each bus stop is assigned a unique `BusStopID`
- Two autonomous buses are spawned with unique `BusID`s
- Each bus:
  1. Drives in autopilot mode
  2. Detects the bus stop signboard
  3. Verifies ID match (`BusID == BusStopID`)
  4. Stops and parks at the correct stop
  5. Waits for 10 seconds
  6. Continues driving autonomously

This ensures correct bus-to-stop assignment without conflicts.

---

## Technologies Used

- CARLA Simulator (0.9.15)
- Unreal Engine
- Python
- Blueprint Visual Scripting
- Autonomous vehicle autopilot
