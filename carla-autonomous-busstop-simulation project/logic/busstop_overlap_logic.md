## Bus Stop Detection Logic (Blueprint)

The bus stop signboard uses an overlap-based detection mechanism.

### Step-by-step Logic

1. OnComponentBeginOverlap is triggered
2. The overlapping actor is cast to CarlaWheeledVehicle
3. A guard condition checks if a bus was already detected
4. The bus ID is compared with the BusStopID
5. If the IDs match:
   - Autopilot is disabled
   - The bus is stopped
   - A waiting timer is started
6. After 10 seconds, autopilot is re-enabled

### Key Variables

- BusStopID (Integer)
- BusID (Integer)
- bBusDetected (Boolean)
- CurrentBus (Vehicle reference)

This design prevents multiple buses from stopping at the same stop
and enables scalable multi-stop scenarios.
