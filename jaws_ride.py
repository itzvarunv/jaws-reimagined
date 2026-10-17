"""
===============================================================================
 JAWS-THEMED PARK  -  ride + show-control visualisation for Blender (bpy)
===============================================================================
 Concept, sequence and safety logic : Varun (handwritten notes, 18 Sep 2025)
 Script                              : written with Claude

 HOW TO RUN
   Blender > Scripting workspace > Open > jaws_ride.py > Run Script (Alt+P).
   WARNING: it wipes the current scene and builds everything from scratch.
   Then press Space to play. Tested on Blender 5.1; written to also run on 4.x.

 HOW IT IS ORGANISED  (everything that moves is driven by the SHOW CONTROLLER)
   1. PARAMETERS      - every size / speed / angle you may want to change (SI)
   2. TRACK           - the hidden underwater "ramp" the boat is fixed to
   3. SHOW CONTROLLER - channels + cue list, like a real park show-control PLC.
                        Every actuator (motion-base arms, shark booms, pistons,
                        LEDs, screens, flash, water jets) is a CHANNEL.  Cues
                        move channels; the controller samples them at FPS and
                        writes keyframes.  Nothing is hand-animated.
   4. RIDE PROGRAM    - your sequence, beat by beat, as cues
   5. SAFETY          - seat-belt interlock: one open belt -> E-STOP, full
                        daylight, boat brakes, all rigs go to home position
   6. GEOMETRY        - hall, lagoon, track, boat, motion base, animatronics,
                        dolphin tank, fish pool, ticket area
   7. BINDINGS        - which channel drives which object property
   Output: a cue sheet (Text editor > SHOW_CUE_SHEET.csv), timeline markers
   for every sound / voice cue, and an actuator stroke report in the console.

 Sizes were not in the notes, so the defaults below are assumptions picked to
 be realistic for a dark boat ride. Change them freely; the layout re-derives.
===============================================================================
"""
import math

try:
    import bpy
    import bmesh
    from mathutils import Matrix
except ImportError:          # lets the controller run in plain Python (cue sheet only)
    bpy = None

# =============================================================================
# 1. PARAMETERS   (metres, seconds, degrees, watts)
# =============================================================================
MODE = "SHOW"                 # "SHOW"  = ride + dolphin night show
                              # "MAINTENANCE" = night drain of lagoon into dolphin tank, day refill
SIMULATE_BELT_FAULT = None    # e.g. {"seat": 7, "time": 60.0}  -> safety interlock demo
SIDE_SCREEN_STORY = True      # blackout: side screens follow the story (True) or stay pitch black (False)
VIEW = "ENGINEERING"          # "ENGINEERING" = extra fill light + see-through water so rigs are visible
                              # "GUEST" = what riders actually see (very dark, only cue-lit scenes)
WATER_SEE_THROUGH = VIEW == "ENGINEERING"
AUTO_RIDER_VIEW = True        # after building, jump to Layout tab, rider camera, Rendered mode
SIGN_TEXT = "YOUR SIGN"       # the light sign at the dolphin tank - type your own text
FPS = 24

# --- main hall + lagoon ------------------------------------------------------
# Ride track = centre-line through these points (x, y) in metres, closed loop, smoothed
# with a Catmull-Rom spline. Point 0 = LOAD (guests board). Point UNLOAD_INDEX = UNLOAD
# (guests leave). From UNLOAD the boat runs EMPTY, behind a show wall, back to LOAD.
TRACK_PTS = [(-30, -18), (-18, -18), (-6, -16), (6, -19), (18, -17), (30, -15),   # 0 LOAD
             (37, -7), (35, 5), (27, 12), (19, 7), (16, -3), (10, -8), (4, -3),     # deep fold
             (3, 7), (-2, 16), (-12, 19), (-22, 15), (-24, 6), (-16, 1),            # hook = empty return
             (-14, -6), (-24, -9), (-34, -4), (-38, -12), (-36, -18)]               # 16 = UNLOAD
UNLOAD_INDEX = 16
LAGOON_HX, LAGOON_HY = 42.0, 25.0   # lagoon half-size (84 m x 50 m)
WATER_DEPTH = 1.5             # main lagoon depth (boat rides are shallow)
HALL_X = (-46.0, 46.0)        # hall walls
HALL_Y = (-36.0, 29.0)
HALL_H = 12.0                 # ceiling = LED screen sky, sprinklers under it
WALKWAY_Z = 0.3

# --- boat + motion base --------------------------------------------------------
BOAT_L, BOAT_B = 8.5, 3.0     # boat length, beam
SEAT_ROWS, SEATS_PER_ROW = 5, 4
ARM_X, ARM_Y = 1.4, 0.8       # the 4 arms sit at (+-ARM_X, +-ARM_Y) under the hull (your sketch)
CARRIAGE_Z = -1.25            # underwater carriage running on the rail
HULL_BOTTOM = -0.5            # hull bottom at rest (draft 0.5 m)
CRUISE = 1.2                  # m/s
RETURN_SPEED = 1.3
TRANSFER_SPEED = 2.0          # empty boat, unload -> load
UNLOAD_DWELL = 12.0           # s for guests to unbuckle and step off
DOCK_DECEL = 0.4              # m/s^2  (gentle stop at the dock)
ESTOP_DECEL = 1.0             # m/s^2  (controlled emergency stop)

# --- animatronic shark rigs (hydraulic boom in a floor pit + wrist + jaw) ----
BOOM_LEN = 4.5
PIT_Z = -2.6                  # boom hinge depth (pit below the 1.5 m floor)
BOOM_REST = -8.0              # parked angle, fully hidden
WRIST_K = 0.75                # wrist actuator cancels 75 % of boom pitch so the shark stays level
HEAD_WRIST_K = 0.3            # head-only rigs: wrist cancels less, so the head rears up out of the water
HEAD_BOOM = 31.0              # boom angle when a head-only rig strikes (cut face stays under water)
# Only build what guests actually see in each scene (cheaper, fewer actuators):
SHARK_KIND = {"S1": "fin",    # brush: dorsal fin + back slides along the hull
              "P1": "head", "P2": "head", "P3": "head", "P4": "head",   # pop-ups
              "S3": "head",   # rams the left side
              "S4": "full",   # breaches over the man, resurfaces, death roll -> needs whole body
              "DS": "head"}   # dolphin tank lunge
SHARK_LEN = 7.0

# --- entrance: dolphin night-show tank, fish pool, tickets --------------------
DOLPHIN_TANK_C = (0.0, -72.0)
DOLPHIN_TANK_R = 24.0
DOLPHIN_TANK_DEPTH = 7.0      # water depth during the show
DOLPHIN_RIM = 4.0             # tank wall height above show water level
DOLPHIN_BOOM = 3.6
DOLPHIN_PIT_Z = -2.6
FISH_POOL_C = (36.0, -48.0)
FISH_POOL_R = 5.0
TICKET_C = (-20.0, -42.0)

DEG = math.pi / 180.0


# =============================================================================
# 2. TRACK  (the "ramp": a rail on the lagoon floor, boat course preprogrammed)
# =============================================================================
def _build_track(samples=40):
    P = TRACK_PTS
    n = len(P)
    pts = []
    for i in range(n):
        p0, p1, p2, p3 = P[i - 1], P[i], P[(i + 1) % n], P[(i + 2) % n]
        for k in range(samples):
            u = k / samples
            u2, u3 = u * u, u * u * u
            pts.append(tuple(0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * u + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * u2
                                    + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * u3) for j in (0, 1)))
    pts.append(pts[0])
    cum = [0.0]
    seg_h = []
    for i in range(len(pts) - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        cum.append(cum[-1] + math.hypot(dx, dy))
        h = math.atan2(dy, dx)
        if seg_h:                                    # unwrap so heading is continuous
            while h - seg_h[-1] > math.pi:
                h -= 2 * math.pi
            while h - seg_h[-1] < -math.pi:
                h += 2 * math.pi
        seg_h.append(h)
    node_h = [seg_h[0]] + [0.5 * (seg_h[i - 1] + seg_h[i]) for i in range(1, len(seg_h))] + [seg_h[-1]]
    return pts, cum, node_h, cum[UNLOAD_INDEX * samples]


_TRK_PTS, _TRK_CUM, _TRK_H, S_UNLOAD = _build_track()
TRACK_LEN = _TRK_CUM[-1]


def track_point(s):
    """(x, y, heading) at arc length s along the ride track. LOAD station at s = 0."""
    import bisect
    s = max(0.0, min(TRACK_LEN, s))
    i = max(0, min(len(_TRK_CUM) - 2, bisect.bisect_right(_TRK_CUM, s) - 1))
    f = (s - _TRK_CUM[i]) / max(1e-9, _TRK_CUM[i + 1] - _TRK_CUM[i])
    (x0, y0), (x1, y1) = _TRK_PTS[i], _TRK_PTS[i + 1]
    return (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, _TRK_H[i] + (_TRK_H[i + 1] - _TRK_H[i]) * f)


def track_is_curve(s):
    return abs(track_point(s + 3)[2] - track_point(s - 3)[2]) / 6.0 > 1 / 30.0


def aim_rig(s_hit, lat_hit, reach, side, off_deg):
    """Place a boom rig so the shark's nose lands on (s_hit, lat_hit).
    The rig sits AHEAD of the hit point, angled off_deg away from the track on `side`
    (+1 left, -1 right), boom pointing back at the oncoming boat. Small footprint, so
    tracks can snake close together. Returns (x, y, yaw) of the hinge."""
    a = off_deg * DEG
    tx, ty, _ = beside(s_hit, lat_hit)
    hx, hy, _ = beside(s_hit + reach * math.cos(a), lat_hit + side * reach * math.sin(a))
    yaw = math.atan2(ty - hy, tx - hx)
    # on curves, slide the hinge along that line so the reach is exact
    return (tx - reach * math.cos(yaw), ty - reach * math.sin(yaw), yaw)


def head_reach(boom):
    return BOOM_LEN * math.cos(boom * DEG) + 0.5 * SHARK_LEN * math.cos((1 - HEAD_WRIST_K) * boom * DEG)


def beside(s, lateral, along=0.0):
    """World (x, y, heading) of a point beside the track.
    lateral: + = guests' LEFT, - = guests' RIGHT.  along: + = ahead."""
    x, y, yaw = track_point(s + along)
    return (x - math.sin(yaw) * lateral, y + math.cos(yaw) * lateral, yaw)


# =============================================================================
# 3. SHOW CONTROLLER  (channels + cues)
# =============================================================================
def _ease(u, kind):
    u = max(0.0, min(1.0, u))
    if kind == "linear":
        return u
    if kind == "in":        # accelerating (e.g. pulled under)
        return u * u
    if kind == "out":       # decelerating (e.g. impact)
        return 1 - (1 - u) * (1 - u)
    if kind == "step":
        return 1.0 if u >= 1.0 else 0.0
    return u * u * (3 - 2 * u)          # "smooth" S-curve motion profile


class Channel:
    """One actuator output. Keys are (time, value, ease-into-this-key)."""

    def __init__(self, name, rest, unit, device):
        self.name, self.rest, self.unit, self.device = name, rest, unit, device
        self.keys = [(0.0, rest, "linear")]

    def value(self, t):
        k = self.keys
        if t <= k[0][0]:
            return k[0][1]
        for i in range(1, len(k)):
            if t <= k[i][0]:
                t0, v0, _ = k[i - 1]
                t1, v1, e = k[i]
                if t1 - t0 < 1e-9:
                    return v1
                return v0 + (v1 - v0) * _ease((t - t0) / (t1 - t0), e)
        return k[-1][1]

    def last_t(self):
        return self.keys[-1][0]

    def move(self, t0, t1, v, ease="smooth"):
        """Hold the current value until t0, then move to v, arriving at t1."""
        if t0 < self.last_t() - 1e-6:
            raise ValueError("Cue for %s at %.2fs overlaps its previous move (ends %.2fs)"
                             % (self.name, t0, self.last_t()))
        self.keys.append((t0, self.value(t0), "linear"))
        self.keys.append((t1, v, ease))
        return self

    def pulse(self, t, peak, rise, hold, fall, ease_up="out", ease_down="in"):
        base = self.value(t)
        self.move(t, t + rise, peak, ease_up)
        self.move(t + rise + hold, t + rise + hold + fall, base, ease_down)
        return self

    def wobble(self, t0, t1, fn, step=0.2, ease="smooth"):
        """Programmed oscillation: value = fn(t) sampled every `step` seconds."""
        t = t0
        self.move(t0, t0, self.value(t0), "linear")
        while t < t1 - 1e-9:
            t = min(t1, t + step)
            self.keys.append((t, fn(t), ease))
        return self


class Show:
    def __init__(self):
        self.ch = {}
        self.markers = []      # (time, text)  -> timeline markers (sound / voice / events)
        self.cues = []         # (time, device, action) -> cue sheet
        self.layout = {}       # where each show unit stands (derived from the ride program)
        self.rain = (0.0, 0.0)
        self.duration = 10.0
        self.fault = None
        self.path = None

    def c(self, name, rest=0.0, unit="", device=""):
        if name not in self.ch:
            self.ch[name] = Channel(name, rest, unit, device or name.split(".")[0])
        return self.ch[name]

    def cue(self, t, device, action, marker=False):
        self.cues.append((t, device, action))
        if marker:
            self.markers.append((t, "%s: %s" % (device, action)))

    def sfx(self, t, text):
        self.cue(t, "AUDIO", text, marker=True)


class BoatPath:
    """Integrates the boat speed channel -> distance along the track."""

    def __init__(self, speed_ch, t_end, dt=1.0 / 96):
        self.dt = dt
        self.s = [0.0]
        s, t = 0.0, 0.0
        while t < t_end + dt:
            v0, v1 = speed_ch.value(t), speed_ch.value(t + dt)
            s = min(TRACK_LEN, s + 0.5 * (v0 + v1) * dt)
            t += dt
            self.s.append(s)

    def at(self, t):
        i = t / self.dt
        i0 = max(0, min(len(self.s) - 2, int(i)))
        f = i - i0
        return self.s[i0] * (1 - f) + self.s[i0 + 1] * f


# =============================================================================
# 4. RIDE PROGRAM  (Varun's sequence, as cues)
# =============================================================================
SEATS = SEAT_ROWS * SEATS_PER_ROW
T = dict(dispatch=3.0, capsize=20.0, brush=34.0, rescue=38.0, ram=46.0,
         chaos=50.0, blackout=66.5, sky_back=90.0, left_hit=94.0,
         human=98.5, crash=103.2, resurface=106.0, dead=111.0, show_end=113.0)


def ride_speed_program(sh):
    v = sh.c("boat.speed", 0.0, "m/s", "BOAT TRACK DRIVE")
    v.move(T["dispatch"], T["dispatch"] + 3.0, CRUISE)                    # leave dock
    v.move(T["rescue"], T["rescue"] + 1.5, 1.7)                          # 'driver tries to rescue'
    v.move(T["ram"], T["ram"] + 0.4, 0.9, "out")                         # ram: speed knocked off
    v.move(T["ram"] + 1.2, T["ram"] + 2.5, 1.7)
    v.move(T["chaos"], T["chaos"] + 1.5, 2.6)                            # speed increased
    for t, vv in ((54.0, 2.1), (56.5, 2.8), (59.0, 2.2), (61.5, 2.9), (64.0, 2.4)):
        v.move(t - 1.0, t, vv)                                           # erratic surges
    v.move(T["blackout"] - 1.0, T["blackout"] + 1.0, 0.8)                # drift in the dark
    v.move(T["sky_back"], T["sky_back"] + 1.0, 1.0)
    v.move(T["left_hit"], T["left_hit"] + 0.3, 0.5, "out")               # impact
    v.move(T["left_hit"] + 1.5, T["left_hit"] + 3.0, 1.0)
    v.move(T["human"] + 2.0, T["human"] + 3.5, 0.6)                      # slow near the man
    v.move(T["resurface"], T["resurface"] + 1.0, 0.35)                   # hold position while firing
    v.move(T["show_end"], T["show_end"] + 2.0, RETURN_SPEED)
    # --- return to UNLOAD: cruise, then constant decel to stop exactly at S_UNLOAD
    t0 = T["show_end"] + 2.0
    path = BoatPath(v, t0)
    remaining = S_UNLOAD - path.at(t0)
    d_brake = RETURN_SPEED ** 2 / (2 * DOCK_DECEL)
    if remaining < d_brake:
        raise ValueError("Track too short for the program: move UNLOAD_INDEX later or add "
                         "TRACK_PTS (need %.1f m more before unload)." % (d_brake - remaining))
    t_brake = t0 + (remaining - d_brake) / RETURN_SPEED
    t_unload = t_brake + RETURN_SPEED / DOCK_DECEL
    v.move(t_brake, t_unload, 0.0, "linear")
    # --- guests leave, then the EMPTY boat transfers behind the show wall to LOAD
    t_go = t_unload + UNLOAD_DWELL
    v.move(t_go, t_go + TRANSFER_SPEED / DOCK_DECEL, TRANSFER_SPEED, "linear")
    t1 = t_go + TRANSFER_SPEED / DOCK_DECEL
    path = BoatPath(v, t1)
    remaining = TRACK_LEN - path.at(t1)
    d_brake = TRANSFER_SPEED ** 2 / (2 * DOCK_DECEL)
    t_brake = t1 + max(0.0, remaining - d_brake) / TRANSFER_SPEED
    t_load = t_brake + TRANSFER_SPEED / DOCK_DECEL
    v.move(t_brake, t_load, 0.0, "linear")
    return t_unload, t_go, t_load


def shark_channels(sh, u):
    dev = "SHARK RIG " + u
    return (sh.c(u + ".boom", BOOM_REST, "deg", dev), sh.c(u + ".jaw", 0.0, "deg", dev),
            sh.c(u + ".roll", 0.0, "deg", dev), sh.c(u + ".sled", 0.0, "m", dev))


def build_ride(sh):
    t_unload, t_go, t_dock = ride_speed_program(sh)
    path = BoatPath(sh.ch["boat.speed"], t_dock + 1)
    sh.path = path
    S = path.at
    roll = sh.c("boat.roll", 0.0, "deg", "MOTION BASE")      # + = lean right
    pitch = sh.c("boat.pitch", 0.0, "deg", "MOTION BASE")    # + = bow up
    heave = sh.c("boat.heave", 0.0, "m", "MOTION BASE")
    wheel = sh.c("boat.wheel", 0.0, "deg", "OPERATOR (fake)")
    sky_n = sh.c("sky.night", 1.0, "", "CEILING SCREENS")
    sky_d = sh.c("sky.day", 0.0, "", "CEILING SCREENS")
    side_n = sh.c("side.night", 1.0, "", "SIDE SCREENS")
    side_s = sh.c("side.story", 0.0, "", "SIDE SCREENS")
    side_d = sh.c("side.day", 0.0, "", "SIDE SCREENS")
    sh.c("house.lights", 0.0, "", "HOUSE LIGHTS")
    flash = sh.c("gun.flash", 0.0, "", "MUZZLE FLASH STROBE")
    for i in range(SEATS):
        sh.c("seat.%02d" % (i + 1), 1.0, "1=belt latched", "SEAT SENSOR %02d" % (i + 1))
    sh.layout["spots"] = {}

    def scene_spot(name, xy, t_on, t_off):
        """Hidden ceiling spot that lights ONE scene only while it plays (the illusion)."""
        sp = sh.c("spot." + name, 0.0, "", "SCENE SPOT " + name.upper())
        sp.move(t_on - 0.6, t_on, 1.0)
        sp.move(t_off, t_off + 0.8, 0.0)
        sh.layout["spots"][name] = xy[:2]

    sh.cue(0.0, "SPRINKLERS", "rain ON (ceiling sprinklers)")
    sh.cue(0.0, "CEILING SCREENS", "night sky")
    sh.cue(0.0, "SEAT SENSORS", "all 3-point belts latched -> dispatch permitted")
    sh.sfx(1.0, "engine start, operator + crew scripted banter (operator fakes steering)")
    sh.cue(T["dispatch"], "BOAT TRACK DRIVE", "dispatch, ramp to %.1f m/s" % CRUISE)

    # operator's wheel: fake steering that follows the curves + idle jiggle
    def wheel_fn(t):
        s = S(t)
        base = 35.0 if track_is_curve(s) else 0.0
        return base + 8 * math.sin(1.3 * t) + 4 * math.sin(3.1 * t + 1)
    wheel.wobble(T["dispatch"], T["chaos"], wheel_fn, 0.4)

    # ---- SCENE 1: capsized boat, arm pops up, climbs, yells, pulled under -----
    t1 = T["capsize"]
    s_cap = S(t1 + 6.0)
    sh.layout["capsized"] = beside(s_cap, -5.5)
    sh.layout["victim1"] = beside(s_cap, -4.0, -0.6)
    scene_spot("capsize", sh.layout["victim1"], t1 + 0.5, t1 + 8.0)
    lift = sh.c("v1.lift", 0.0, "0=hidden 1=up", "VICTIM-1 ARM PISTON")
    sho = sh.c("v1.shoulder", 0.0, "deg", "VICTIM-1 ARM")
    elb = sh.c("v1.elbow", 0.0, "deg", "VICTIM-1 ARM")
    lift.move(t1 + 1.0, t1 + 1.4, 1.0, "out")
    sh.cue(t1 + 1.0, "VICTIM-1 ARM PISTON", "arm + top of head pop up beside capsized hull")
    sho.wobble(t1 + 1.4, t1 + 6.6, lambda t: 75 + 35 * math.sin(2 * math.pi * 1.3 * (t - t1)), 0.12)
    elb.wobble(t1 + 1.4, t1 + 6.6, lambda t: 45 + 25 * math.sin(2 * math.pi * 1.3 * (t - t1) + 1.2), 0.12)
    sh.sfx(t1 + 1.5, "VICTIM: 'Help! Help me!'")
    sh.sfx(t1 + 4.0, "VICTIM: 'Over here! Please!'")
    sh.sfx(t1 + 6.6, "VICTIM: short yell")
    lift.move(t1 + 6.8, t1 + 7.1, -0.25, "in")                            # yanked under
    sho.move(t1 + 6.8, t1 + 7.1, 160)
    sh.cue(t1 + 6.8, "VICTIM-1 ARM PISTON", "fast retract 0.3 s (pulled under)")
    lift.move(t1 + 8.0, t1 + 9.0, 0.0)

    # ---- SCENE 2: shark brushes the boat, tilt 5-7 deg sideways -----------------
    t2 = T["brush"]
    s2 = S(t2 + 1.5)
    yaw_unit = track_point(s2)[2] + math.pi                               # swims against the boat
    tip = BOOM_LEN * math.cos(24 * DEG)
    hx, hy, _ = beside(s2, -3.3)
    hx -= math.cos(yaw_unit) * tip
    hy -= math.sin(yaw_unit) * tip
    sh.layout["S1"] = (hx, hy, yaw_unit)
    scene_spot("brush", beside(s2, -3.0), t2 - 0.2, t2 + 3.2)
    boom, jaw, _, sled = shark_channels(sh, "S1")
    sled.move(0, 0.01, -9.0)
    boom.move(t2 - 1.0, t2 - 0.2, 24)                                     # dorsal fin breaks surface
    sled.move(t2, t2 + 3.0, 9.0, "linear")                                # 6 m/s pass along the hull
    sh.cue(t2, "SHARK RIG S1", "sled pass along right side, fin up")
    roll.move(t2 + 1.2, t2 + 1.7, 6.0, "out")                             # 6 deg (5-7 deg)
    roll.move(t2 + 1.9, t2 + 3.4, 0.0)
    sh.cue(t2 + 1.2, "MOTION BASE", "roll 6 deg right (brush)")
    sh.sfx(t2 + 1.2, "hull scrape + thud")
    boom.move(t2 + 3.0, t2 + 3.8, BOOM_REST)

    # ---- SCENE 3: driver 'rescues', bow lifts 15 deg as if rammed, drops -------
    sh.sfx(T["rescue"], "OPERATOR: 'Hang on, we're going for him!'  engine throttle up")
    t3 = T["ram"]
    pitch.move(t3, t3 + 0.45, 15.0, "out")
    heave.move(t3, t3 + 0.45, 0.45, "out")
    sh.cue(t3, "MOTION BASE", "bow up 15 deg in 0.45 s (rammed from below)")
    sh.sfx(t3, "BOOM - ram impact under the bow")
    pitch.move(t3 + 1.0, t3 + 1.35, -3.0, "in")
    heave.move(t3 + 1.0, t3 + 1.35, -0.08, "in")
    sh.sfx(t3 + 1.3, "splash as the bow drops")
    pitch.move(t3 + 1.35, t3 + 2.1, 0.0)
    heave.move(t3 + 1.35, t3 + 2.1, 0.0)

    # ---- SCENE 4: speed up, erratic motion, shark pops up here and there --------
    t4 = T["chaos"]
    t4e = T["blackout"] - 0.5
    sh.cue(t4, "BOAT TRACK DRIVE", "speed up to 2.6 m/s with surges")

    def env(t):
        return min(1.0, (t - t4) / 1.5, max(0.0, (t4e - t) / 1.5))
    roll.wobble(t4, t4e, lambda t: env(t) * (4.0 * math.sin(2 * math.pi * 0.7 * t)
                                            + 2.0 * math.sin(2 * math.pi * 1.6 * t + 1)), 0.25)
    pitch.wobble(t4, t4e, lambda t: env(t) * (2.5 * math.sin(2 * math.pi * 0.9 * t + 2)
                                             + 1.0 * math.sin(2 * math.pi * 2.1 * t)), 0.25)
    wheel.wobble(t4, t4e, lambda t: 70 * math.sin(2 * math.pi * 0.55 * t) * env(t)
                 + (35 if track_is_curve(S(t)) else 0), 0.25)
    side = -1
    for i, tp in enumerate((53.0, 56.5, 59.5, 62.5)):
        u = "P%d" % (i + 1)
        sp = S(tp) + 3.0
        sd = side
        side = -side
        hx, hy, _ = aim_rig(sp, 3.6 * sd, head_reach(HEAD_BOOM), sd, 20)
        if abs(hx) > LAGOON_HX - 3 or abs(hy) > LAGOON_HY - 3:       # no room: use other side
            sd = -sd
        # head-only rig just ahead of the boat, angled 20 deg off the track
        sh.layout[u] = aim_rig(sp, 3.6 * sd, head_reach(HEAD_BOOM), sd, 20)
        scene_spot(u.lower(), beside(sp, 3.5 * sd), tp - 0.2, tp + 1.2)
        b, j, _, _ = shark_channels(sh, u)
        b.move(tp - 0.4, tp + 0.15, HEAD_BOOM, "out")
        j.move(tp, tp + 0.2, 30)
        j.move(tp + 0.7, tp + 0.9, 0)
        b.move(tp + 1.0, tp + 1.6, BOOM_REST, "in")
        sh.cue(tp - 0.4, "SHARK RIG " + u, "pop-up (%s side)" % ("left" if sd > 0 else "right"))
        sh.sfx(tp, "water burst + guests' side hiss")
    wheel.move(t4e, t4e + 1.0, 0.0)

    # ---- SCENE 5: blackout + narrator ------------------------------------------
    t5 = T["blackout"]
    sky_n.move(t5 - 0.5, t5 + 0.5, 0.0)
    side_n.move(t5 - 0.5, t5 + 0.5, 0.0)
    sh.cue(t5 - 0.5, "CEILING SCREENS", "fade to pitch black (1 s)")
    sh.sfx(t5, "engine cut to idle")
    if SIDE_SCREEN_STORY:
        side_s.wobble(t5 + 2.0, T["sky_back"] - 1.5,
                      lambda t: 0.55 + 0.35 * math.sin(1.7 * t) * math.sin(0.6 * t + 1), 0.5)
        side_s.move(T["sky_back"] - 1.5, T["sky_back"] - 0.5, 0.0)
        sh.cue(t5 + 2.0, "SIDE SCREENS", "story imagery (dim, flickering)")
    else:
        sh.cue(t5 + 2.0, "SIDE SCREENS", "stay pitch black")
    narr = ["NARRATOR: 'Long before this harbour had a name, fishermen went out on nights like this...'",
            "NARRATOR: '...and some never came back. Their boats were found empty, torn open.'",
            "NARRATOR: 'Our grandfathers tried. Nets, harpoons, prayers. Nothing stopped it.'",
            "NARRATOR: 'It is still out there. Someone has to end it. Tonight, that is us.'"]
    for k, line in enumerate(narr):
        sh.sfx(t5 + 2.0 + 5.5 * k, line)

    # ---- SCENE 6: sky returns, shark hits from left, boat tilts 20 deg left -----
    t6 = T["sky_back"]
    sky_n.move(t6, t6 + 1.5, 1.0)
    side_n.move(t6, t6 + 1.5, 1.0)
    sh.cue(t6, "CEILING SCREENS", "night sky returns (1.5 s)")
    th = T["left_hit"]
    sh_ = S(th)
    sh.layout["S3"] = aim_rig(sh_, 1.6, head_reach(HEAD_BOOM), +1, 40)    # comes in from the LEFT
    scene_spot("lefthit", beside(sh_, 3.0), th - 0.6, th + 1.4)
    b, j, _, _ = shark_channels(sh, "S3")
    b.move(th - 0.8, th - 0.1, HEAD_BOOM, "out")
    j.move(th - 0.5, th - 0.2, 32)
    sh.cue(th - 0.8, "SHARK RIG S3", "lunge at left side")
    roll.move(th, th + 0.4, -20.0, "out")
    pitch.move(th, th + 0.3, 3.0, "out")
    sh.cue(th, "MOTION BASE", "roll 20 deg LEFT in 0.4 s (impact)")
    sh.sfx(th, "SLAM - impact on the left side, crew: 'Hold on!'")
    j.move(th + 0.3, th + 0.5, 0)
    b.move(th + 0.6, th + 1.6, BOOM_REST, "in")
    sh.cue(th + 0.6, "SHARK RIG S3", "retract & hide while boat stabilises")
    roll.wobble(th + 0.4, th + 4.4,
                lambda t: -20.0 * math.exp(-1.1 * (t - th - 0.4)) * math.cos(2 * math.pi * 0.45 * (t - th - 0.4)), 0.2)
    roll.move(th + 4.4, th + 5.0, 0.0)
    pitch.move(th + 0.5, th + 1.5, 0.0)
    sh.cue(th + 0.4, "MOTION BASE", "damped stabilisation (4 s)")

    # ---- SCENE 7: another shark crashes onto a man who fell off a boat ----------
    t7 = T["human"]
    s7 = S(T["crash"] + 0.3)
    sh.layout["human2"] = beside(s7, -5.5)
    scene_spot("human", beside(s7, -6.0), T["human"], T["dead"] + 2.5)
    # full shark rig in line with the man, 7 m ahead of him: rises short of him, slams onto him
    sh.layout["S4"] = aim_rig(s7, -5.5, 7.0, -1, 0)
    hl = sh.c("h2.lift", 0.0, "0=hidden 1=up", "VICTIM-2 PISTON")
    hw = sh.c("h2.wave", 20.0, "deg", "VICTIM-2 ARMS")
    hl.move(t7, t7 + 0.8, 1.0)
    sh.cue(t7, "VICTIM-2 PISTON", "man rises, waving")
    hw.wobble(t7 + 0.8, T["crash"] + 0.1, lambda t: 95 + 55 * math.sin(2 * math.pi * 1.1 * (t - t7)), 0.12)
    sh.sfx(t7 + 1.0, "MAN: 'HELP! Over here! I fell off the boat!'")
    sh.sfx(t7 + 3.0, "MAN: 'Please, hurry!'")
    b, j, r, _ = shark_channels(sh, "S4")
    tc = T["crash"]
    b.move(tc - 1.0, tc - 0.2, 55, "out")
    j.move(tc - 0.8, tc - 0.5, 35)
    sh.cue(tc - 1.0, "SHARK RIG S4", "breach and crash down onto victim-2")
    b.move(tc, tc + 0.4, 18, "in")
    hl.move(tc + 0.1, tc + 0.5, -0.2, "in")
    j.move(tc + 0.3, tc + 0.5, 0)
    sh.sfx(tc + 0.2, "CRASH + big splash, scream cut off")
    b.move(tc + 0.6, tc + 1.4, BOOM_REST)
    hl.move(tc + 1.5, tc + 2.5, 0.0)

    # ---- SCENE 8: staff fire, a few misses, shark dies ---------------------------
    t8 = T["resurface"]
    b.move(t8, t8 + 0.8, 36, "out")
    sh.cue(t8, "SHARK RIG S4", "resurfaces at the boat, jaw snapping")
    j.wobble(t8 + 0.8, 110.3, lambda t: 15 + 15 * math.sin(2 * math.pi * 1.4 * t), 0.15)
    shots = [(107.2, "MISS", "g1"), (107.9, "MISS", "g2"), (108.7, "MISS", "g3"),
             (109.6, "HIT", None), (110.2, "HIT", None)]
    sh.layout["geysers"] = {"g1": beside(s7, -6.2, 2.0), "g2": beside(s7, -7.6, -1.6),
                            "g3": beside(s7, -5.0, -3.2)}
    for ts, res, g in shots:
        flash.move(ts, ts + 0.03, 1.0, "linear")
        flash.move(ts + 0.08, ts + 0.12, 0.0, "linear")
        sh.sfx(ts, "CREW fires (%s)" % res)
        if g:
            gc = sh.c(g + ".jet", 0.0, "0..1 height", "WATER JET " + g.upper())
            gc.move(ts + 0.12, ts + 0.3, 1.0, "out")
            gc.move(ts + 0.6, ts + 1.3, 0.0, "in")
            sh.cue(ts + 0.12, "WATER JET " + g.upper(), "pneumatic burst = bullet miss")
    td = T["dead"]
    j.move(110.3, 110.5, 18)
    r.move(td - 0.6, td + 0.8, 160)
    b.move(td, td + 2.5, BOOM_REST, "in")
    sh.cue(td - 0.6, "SHARK RIG S4", "death roll belly-up, slow sink")
    sh.sfx(td + 1.0, "shark groan, silence, crew cheers")
    sh.sfx(T["show_end"], "OPERATOR outro, engine back to cruise, return to dock")
    sh.cue(t_unload, "BOAT TRACK DRIVE", "stopped at UNLOAD station, belts release, guests exit")
    sh.cue(t_go, "BOAT TRACK DRIVE", "EMPTY transfer behind show wall to LOAD station")
    sh.cue(t_dock, "BOAT TRACK DRIVE", "stopped at LOAD station, next guests board")
    sh.layout["dock"] = beside(0.0, -3.2)
    sh.layout["unload"] = beside(S_UNLOAD, -3.2)

    sh.rain = (0.0, t_dock)
    return t_dock


def build_dolphin_show(sh):
    """Compressed version of the 30-min night show (real time would be 30 min)."""
    cx, cy = DOLPHIN_TANK_C
    show = sh.c("dshow.lights", 1.0, "", "DOLPHIN SHOW SPOTS")
    led = sh.c("led.red", 0.0, "", "RED UNDERWATER LEDs")
    sign = sh.c("sign", 0.0, "", "LIGHT SIGN")
    omega = 72.0                       # deg/s -> 5 s per leap cycle
    t_end = 36.0
    for k in range(3):
        u = "dol%d" % (k + 1)
        ang = (-60 + 60 * k) * DEG
        hx, hy = cx + 7.0 * math.cos(ang), cy + 7.0 * math.sin(ang)
        sh.layout[u] = (hx, hy, ang + math.pi / 2)
        phi = sh.c(u + ".phi", 200.0 - 50 * k, "deg", "DOLPHIN RIG %d" % (k + 1))
        p0 = phi.rest
        phi.move(2.0, t_end, p0 - omega * (t_end - 2.0), "linear")
    sh.cue(2.0, "DOLPHIN SHOW", "(compressed) 30-min show: 3 animatronic dolphins leaping")
    # final: dolphin 2 slows at the surface, shark lunges, drags it under
    d2 = sh.ch["dol2.phi"]
    cur = d2.value(t_end)
    target = cur - ((cur - 48.0) % 360.0)
    d2.move(t_end, t_end + 1.2, target, "out")
    for u in ("dol1", "dol3"):
        c = sh.ch[u + ".phi"]
        cur = c.value(t_end)
        c.move(t_end, t_end + 1.5, cur - ((cur - 270.0) % 360.0), "out")   # dive & park at bottom
    ta = 38.0
    hx, hy, yw = sh.layout["dol2"]
    dx, dy = math.cos(yw), math.sin(yw)
    tgt = (hx + dx * DOLPHIN_BOOM * math.cos(48 * DEG), hy + dy * DOLPHIN_BOOM * math.cos(48 * DEG))
    reach = BOOM_LEN * math.cos(HEAD_BOOM * DEG) + 0.5 * SHARK_LEN * math.cos((1 - HEAD_WRIST_K) * HEAD_BOOM * DEG)
    ox, oy = tgt[0] - cx, tgt[1] - cy
    n = math.hypot(ox, oy)
    ux, uy = -ox / n, -oy / n                                               # shark comes from the centre side
    sh.layout["DS"] = (tgt[0] + ux * reach, tgt[1] + uy * reach, math.atan2(-uy, -ux))
    b, j, _, _ = shark_channels(sh, "DS")
    b.move(ta - 0.7, ta, HEAD_BOOM, "out")
    j.move(ta - 0.5, ta - 0.2, 35)
    j.move(ta + 0.1, ta + 0.3, 5)
    sh.cue(ta - 0.7, "SHARK RIG DS", "lunges at dolphin 2")
    d2.move(ta + 0.25, ta + 0.8, target - 110, "in")
    b.move(ta + 0.3, ta + 1.0, BOOM_REST, "in")
    sh.cue(ta + 0.25, "DOLPHIN RIG 2", "dragged under")
    sh.sfx(ta, "DOLPHIN SHOW: screams, splash, music stops")
    show.move(ta + 0.3, ta + 0.8, 0.0)
    led.move(ta + 0.5, ta + 3.5, 1.0)
    sh.cue(ta + 0.5, "RED UNDERWATER LEDs", "ramp up over 3 s (water lit red)")
    sign.wobble(ta + 3.0, ta + 4.5, lambda t: 1.0 if int(t * 10) % 3 else 0.2, 0.1, "step")
    sign.move(ta + 4.5, ta + 4.6, 1.0, "linear")
    sh.cue(ta + 3.0, "LIGHT SIGN", "flickers on")


def build_maintenance(sh):
    lag = sh.c("lagoon.level", 0.0, "m", "MAIN LAGOON LEVEL")
    dt_ = sh.c("dtank.level", 0.0, "m", "DOLPHIN TANK LEVEL")
    pump = sh.c("pump.rot", 0.0, "deg", "TRANSFER PUMP")
    sh.c("house.lights", 1.0, "", "HOUSE LIGHTS")
    sh.c("sky.night", 0.0, "", "CEILING SCREENS")
    sh.c("side.night", 0.0, "", "SIDE SCREENS")
    sh.c("dshow.lights", 0.0, "", "DOLPHIN SHOW SPOTS")
    sh.c("boat.speed", 0.0, "m/s", "BOAT TRACK DRIVE")
    for k in range(3):
        sh.c("dol%d.phi" % (k + 1), 270.0, "deg", "DOLPHIN RIG %d" % (k + 1))
    vol = 2 * LAGOON_HX * 2 * LAGOON_HY * (WATER_DEPTH - 0.05)
    dh = vol / (math.pi * DOLPHIN_TANK_R ** 2)
    sh.cue(0.0, "MAINTENANCE", "night: lagoon %.0f m^3 -> dolphin tank (+%.2f m)" % (vol, dh), True)
    lag.move(5.0, 65.0, -(WATER_DEPTH - 0.05), "linear")
    dt_.move(5.0, 65.0, dh, "linear")
    pump.move(5.0, 65.0, 360 * 60, "linear")
    sh.cue(66.0, "MAINTENANCE", "lagoon dry: engineers service track, rigs, arms safely", True)
    sh.cue(75.0, "MAINTENANCE", "day: water pumped back, dolphin rigs serviced in shallower tank", True)
    lag.move(75.0, 135.0, 0.0, "linear")
    dt_.move(75.0, 135.0, 0.0, "linear")
    pump.move(75.0, 135.0, 0.0, "linear")
    sh.layout["dock"] = beside(0.0, -3.2)
    sh.layout["unload"] = beside(S_UNLOAD, -3.2)
    sh.duration = 140.0
    sh.path = BoatPath(sh.ch["boat.speed"], sh.duration)
    return sh


# =============================================================================
# 5. SAFETY INTERLOCK  (seat-belt sensor -> E-STOP, full daylight)
# =============================================================================
def safe_rule(name):
    """Where each channel goes after an E-STOP: (target, ramp seconds) or None = freeze."""
    if name.startswith(("dol", "DS.", "led.", "sign", "dshow")):
        return "ignore"                                   # dolphin tank is a separate attraction
    rules = {"boat.roll": (0, 2.0), "boat.pitch": (0, 2.0), "boat.heave": (0, 2.0),
             "boat.wheel": (0, 1.0), "sky.night": (0, 1.0), "sky.day": (1.0, 1.0),
             "side.night": (0, 1.0), "side.story": (0, 1.0), "side.day": (1.0, 1.0),
             "house.lights": (1.0, 1.0), "gun.flash": (0, 0.05),
             "v1.lift": (0, 1.5), "v1.shoulder": (0, 1.5), "v1.elbow": (0, 1.5),
             "h2.lift": (0, 1.5), "h2.wave": (20, 1.5)}
    if name in rules:
        return rules[name]
    if name.endswith(".boom"):
        return (BOOM_REST, 2.5)
    if name.endswith((".jaw", ".roll")):
        return (0, 1.5)
    if name.endswith(".jet"):
        return (0, 0.3)
    if name.startswith("spot."):
        return (0, 1.0)
    return None


def apply_belt_fault(sh, seat, tf):
    sh.fault = (seat, tf)
    for name, ch in sh.ch.items():
        rule = safe_rule(name)
        if rule == "ignore":
            continue
        v = ch.value(tf)
        ch.keys = [k for k in ch.keys if k[0] < tf] + [(tf, v, "linear")]
        if name == "boat.speed":
            ch.keys.append((tf + 0.25, v, "linear"))           # 0.25 s detection + brake latency
            ch.keys.append((tf + 0.25 + v / ESTOP_DECEL, 0.0, "linear"))
        elif rule:
            ch.keys.append((tf + rule[1], rule[0], "smooth"))
    sc = sh.ch["seat.%02d" % seat]
    sc.keys.append((tf + 0.04, 0.0, "linear"))
    sh.markers = [m for m in sh.markers if m[0] < tf or "DOLPHIN" in m[1]]
    sh.cues = [c for c in sh.cues if c[0] < tf or "DOLPHIN" in c[1] or c[1] in
               ("RED UNDERWATER LEDs", "LIGHT SIGN", "SHARK RIG DS")]
    sh.cue(tf, "SAFETY PLC", "SEAT %02d BELT OPEN -> E-STOP: brake %.1f m/s^2, screens full daylight, "
                             "house lights on, rigs home, rain off" % (seat, ESTOP_DECEL), True)
    sh.rain = (sh.rain[0], tf)
    sh.duration = tf + 12.0
    sh.path = BoatPath(sh.ch["boat.speed"], sh.duration)


def build_program():
    sh = Show()
    if MODE == "MAINTENANCE":
        return build_maintenance(sh)
    t_dock = build_ride(sh)
    build_dolphin_show(sh)
    sh.duration = t_dock + 4.0
    if SIMULATE_BELT_FAULT:
        apply_belt_fault(sh, int(SIMULATE_BELT_FAULT["seat"]), float(SIMULATE_BELT_FAULT["time"]))
    for ch in sh.ch.values():
        ch.keys.sort(key=lambda k: k[0])
    sh.cues.sort(key=lambda c: c[0])
    sh.markers.sort(key=lambda m: m[0])
    return sh


def cue_sheet_csv(sh):
    lines = ["time_s,frame,device,action"]
    for t, d, a in sh.cues:
        lines.append('%.2f,%d,"%s","%s"' % (t, int(round(t * FPS)) + 1, d, a.replace('"', "'")))
    return "\n".join(lines) + "\n"


def actuator_report(sh):
    """Inverse kinematics of the 4-arm motion base: arm length every frame."""
    if "boat.roll" not in sh.ch:
        return "Motion base idle in this mode.\n"
    z0 = -CARRIAGE_Z                            # boat origin (waterline) above the carriage
    base_z = 0.15
    lens = {k: [] for k in ("FL", "FR", "RL", "RR")}
    n = int(sh.duration * FPS)
    for f in range(n):
        t = f / FPS
        r = sh.ch["boat.roll"].value(t) * DEG
        p = -sh.ch["boat.pitch"].value(t) * DEG
        hz = z0 + sh.ch["boat.heave"].value(t)
        for key, (ax, ay) in (("FL", (ARM_X, ARM_Y)), ("FR", (ARM_X, -ARM_Y)),
                              ("RL", (-ARM_X, ARM_Y)), ("RR", (-ARM_X, -ARM_Y))):
            x, y, z = ax, ay, HULL_BOTTOM
            y, z = y * math.cos(r) - z * math.sin(r), y * math.sin(r) + z * math.cos(r)   # roll (X)
            x, z = x * math.cos(p) + z * math.sin(p), -x * math.sin(p) + z * math.cos(p)  # pitch (Y)
            lens[key].append(math.dist((x, y, z + hz), (ax, ay, base_z)))
    rest = -CARRIAGE_Z + HULL_BOTTOM - base_z
    out = ["MOTION-BASE ARM STROKE REPORT (rest length %.3f m)" % rest]
    for k, v in lens.items():
        out.append("  arm %s: min %.3f m  max %.3f m  stroke %.3f m" % (k, min(v), max(v), max(v) - min(v)))
    shortest = min(min(v) for v in lens.values())
    stroke = max(max(v) - min(v) for v in lens.values())
    need = stroke + 0.25
    out.append("  single-stage cylinder closed length ~ stroke + 0.25 m = %.2f m, but shortest arm "
               "length in the program is %.2f m -> use 2-stage telescopic cylinders or sink the "
               "rail into a deeper trench." % (need, shortest) if need > shortest else
               "  single-stage cylinders fit (closed %.2f m <= %.2f m)." % (need, shortest))
    vmax = max(sh.ch["boat.speed"].value(f / FPS) for f in range(n))
    out.append("  boat top speed %.2f m/s, track %.1f m, ride time %.1f s"
               % (vmax, TRACK_LEN, sh.duration))
    return "\n".join(out) + "\n"


# =============================================================================
# 6. GEOMETRY  (bpy)
# =============================================================================
COL = {}


def collection(name):
    if name not in COL:
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
        COL[name] = c
    return COL[name]


def set_input(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return node.inputs[n]
    return None


def set_blend(m):
    for attr, val in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND")):
        if hasattr(m, attr):
            try:
                setattr(m, attr, val)
            except Exception:
                pass


def _new_mat(name):
    m = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        m.use_nodes = True
    return m


def mat_pbr(name, color, rough=0.5, metal=0.0, alpha=1.0):
    m = _new_mat(name)
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    set_input(b, ["Base Color"], (*color, 1))
    set_input(b, ["Roughness"], rough)
    set_input(b, ["Metallic"], metal)
    if alpha < 1:
        set_input(b, ["Alpha"], alpha)
        set_blend(m)
    m.diffuse_color = (*color, alpha)
    return m


def mat_emit(name, layers, viewport=(0.1, 0.1, 0.1)):
    """layers: list of (color or callable(nodes, links)->color socket, strength).
    Returns (material, [emission nodes]) so strengths can be driven by channels."""
    m = _new_mat(name)
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    ems, prev = [], None
    for i, (col, st) in enumerate(layers):
        e = nt.nodes.new("ShaderNodeEmission")
        e.location = (-300, -200 * i)
        if callable(col):
            nt.links.new(col(nt.nodes, nt.links), e.inputs["Color"])
        else:
            e.inputs["Color"].default_value = (*col, 1)
        e.inputs["Strength"].default_value = st
        ems.append(e)
        if prev is None:
            prev = e.outputs[0]
        else:
            a = nt.nodes.new("ShaderNodeAddShader")
            nt.links.new(prev, a.inputs[0])
            nt.links.new(e.outputs[0], a.inputs[1])
            prev = a.outputs[0]
    nt.links.new(prev, out.inputs["Surface"])
    m.diffuse_color = (*viewport, 1)
    return m, ems


def mat_water(name, color, alpha, red_emit=False):
    m = mat_pbr(name, color, 0.06, 0.0, alpha)
    nt = m.node_tree
    b = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 1.5
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    sock = None
    if red_emit:
        set_input(b, ["Emission Color", "Emission"], (1.0, 0.02, 0.01, 1))
        sock = set_input(b, ["Emission Strength"], 0.0)
    return m, sock


def obj_from_bm(name, bm, mat, coll, parent=None, loc=(0, 0, 0), rot=(0, 0, 0), smooth=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    if mat:
        me.materials.append(mat)
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def empty(name, coll, parent=None, loc=(0, 0, 0), rot=(0, 0, 0), size=0.3):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_size = size
    coll.objects.link(ob)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def box(name, size, loc, mat, coll, parent=None, rot=(0, 0, 0)):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    return obj_from_bm(name, bm, mat, coll, parent, loc, rot)


def cyl(name, r, length, mat, coll, parent=None, loc=(0, 0, 0), rot=(0, 0, 0),
        axis="Z", r2=None, seg=16, from_base=True):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=r, radius2=r if r2 is None else r2, depth=length)
    if from_base:
        bmesh.ops.translate(bm, vec=(0, 0, length / 2), verts=bm.verts)
    if axis == "X":
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(math.pi / 2, 3, "Y"))
    elif axis == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Matrix.Rotation(-math.pi / 2, 3, "X"))
    return obj_from_bm(name, bm, mat, coll, parent, loc, rot, smooth=True)


def sphere(name, r, mat, coll, parent=None, loc=(0, 0, 0), scale=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=10, radius=r)
    bmesh.ops.scale(bm, vec=scale, verts=bm.verts)
    return obj_from_bm(name, bm, mat, coll, parent, loc, smooth=True)


def loft(bm, length, rh, rw, profile, x0, n=14, steps=22, u0=0.0, u1=1.0):
    rings = []
    for i in range(steps + 1):
        u = u0 + (u1 - u0) * i / steps
        f, zc = profile(u)
        ring = [bm.verts.new((x0 + u * length, math.cos(2 * math.pi * k / n) * rw * f,
                              math.sin(2 * math.pi * k / n) * rh * f + zc * rh)) for k in range(n)]
        rings.append(ring)
    for i in range(steps):
        for k in range(n):
            bm.faces.new((rings[i][k], rings[i][(k + 1) % n], rings[i + 1][(k + 1) % n], rings[i + 1][k]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])


def fin(bm, pts, thick, axis="Y"):
    """Triangular/quad fin prism; pts are 3D points, extruded +-thick/2 along axis."""
    o = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[axis]
    a = [bm.verts.new(tuple(p[i] - o[i] * thick / 2 for i in range(3))) for p in pts]
    b = [bm.verts.new(tuple(p[i] + o[i] * thick / 2 for i in range(3))) for p in pts]
    bm.faces.new(a)
    bm.faces.new(list(reversed(b)))
    for i in range(len(pts)):
        j = (i + 1) % len(pts)
        bm.faces.new((a[i], a[j], b[j], b[i]))


def build_shark(name, coll, parent, mats, kind="full"):
    """Generic great white, ~7 m. Origin = body centre, nose at +X.
    kind: "full" = whole shark, "head" = front half only (head, jaw, pectorals),
          "fin" = dorsal fin + back section only. Returns the jaw hinge (None for "fin")."""
    L = SHARK_LEN
    u0, u1 = {"full": (0.0, 1.0), "head": (0.5, 1.0), "fin": (0.3, 0.78)}[kind]
    bm = bmesh.new()
    loft(bm, L, 0.75, 0.65,
         lambda u: (max(0.06, math.sin(math.pi * u ** 1.25) ** 0.75), 0.15 * (1 - u) - 0.05), -L / 2,
         u0=u0, u1=u1)
    if kind in ("full", "fin"):
        fin(bm, [(0.1, 0, 0.55), (1.4, 0, 0.55), (0.0, 0, 1.75)], 0.08)                 # dorsal
    if kind == "full":
        fin(bm, [(-L / 2 + 0.4, 0, 0.1), (-L / 2, 0, 0.05), (-L / 2 - 0.9, 0, 1.5)], 0.06)   # tail upper
        fin(bm, [(-L / 2 + 0.4, 0, 0.0), (-L / 2, 0, 0.05), (-L / 2 - 0.6, 0, -0.9)], 0.06)  # tail lower
    if kind in ("full", "head"):
        for sd in (1, -1):
            fin(bm, [(0.9, 0.5 * sd, -0.35), (1.8, 0.5 * sd, -0.35), (0.6, 1.9 * sd, -0.75)], 0.06, "Z")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    body = obj_from_bm(name + "_body (%s)" % kind, bm, mats["shark"], coll, parent, smooth=True)
    if kind == "fin":
        return None
    for sd in (1, -1):
        sphere(name + "_eye", 0.06, mats["black"], coll, body, (L / 2 - 0.9, 0.36 * sd, 0.12))
    jaw = empty(name + "_jaw_hinge", coll, body, (L / 2 - 1.5, 0, -0.32), size=0.2)
    bm = bmesh.new()
    loft(bm, 1.3, 0.16, 0.42, lambda u: (max(0.15, 1 - u ** 2), 0), 0.0, n=10, steps=6)
    jm = obj_from_bm(name + "_lowerjaw", bm, mats["shark_belly"], coll, jaw, smooth=True)
    for i in range(6):
        for sd in (1, -1):
            cyl(name + "_tooth", 0.035, 0.12, mats["teeth"], coll, jm,
                (0.2 + 0.17 * i, 0.3 * sd * (1 - (0.17 * i / 1.3) ** 2), 0.1), r2=0.0, seg=6)
    return jaw


def build_dolphin(name, coll, parent, mat, L=2.6):
    bm = bmesh.new()
    loft(bm, L, 0.3, 0.27, lambda u: (max(0.08, math.sin(math.pi * u ** 1.1) ** 0.7), 0), -L / 2)
    fin(bm, [(-0.1, 0, 0.25), (0.35, 0, 0.25), (-0.25, 0, 0.62)], 0.05)
    fin(bm, [(-L / 2 + 0.1, 0, 0), (-L / 2 - 0.35, 0.45, 0), (-L / 2 - 0.2, 0, 0), (-L / 2 - 0.35, -0.45, 0)], 0.04, "Z")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return obj_from_bm(name, bm, mat, coll, parent, smooth=True)


def build_boat_hull(name, L, B, coll, parent, mat, mat_rim, loc=(0, 0, 0), rot=(0, 0, 0)):
    """Plan shape from the sketch: square stern, pointed bow."""
    def outline(scale):
        bow = L / 2 - 2.2
        pts = [(-L / 2, -B / 2), (bow, -B / 2), (L / 2, 0.0), (bow, B / 2), (-L / 2, B / 2)]
        return [(x * (1 if x > -L / 2 + 0.1 else 1), y * scale) for x, y in pts]
    bm = bmesh.new()
    top = [bm.verts.new((x, y, 0.15)) for x, y in outline(1.0)]
    bot = [bm.verts.new((x * 0.95, y, HULL_BOTTOM)) for x, y in outline(0.72)]
    bm.faces.new(top)
    bm.faces.new(list(reversed(bot)))
    for i in range(len(top)):
        j = (i + 1) % len(top)
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    hull = obj_from_bm(name, bm, mat, coll, parent, loc, rot)
    bm = bmesh.new()
    lo = [bm.verts.new((x, y, 0.15)) for x, y in outline(1.0)]
    hi = [bm.verts.new((x, y, 0.85)) for x, y in outline(1.0)]
    for i in range(len(lo)):
        j = (i + 1) % len(lo)
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    rim = obj_from_bm(name + "_gunwale", bm, mat_rim, coll, hull)
    sol = rim.modifiers.new("thickness", "SOLIDIFY")
    sol.thickness = 0.08
    return hull


def mannequin(name, coll, parent, loc, mat_body, mat_skin, legs=True, rot=(0, 0, 0)):
    root = empty(name, coll, parent, loc, rot, 0.2)
    z = 0.0
    if legs:
        for sd in (1, -1):
            cyl(name + "_leg", 0.08, 0.85, mat_body, coll, root, (0, 0.11 * sd, 0))
        z = 0.85
    cyl(name + "_torso", 0.19, 0.6, mat_body, coll, root, (0, 0, z), r2=0.17)
    sphere(name + "_head", 0.12, mat_skin, coll, root, (0, 0, z + 0.75))
    arms = []
    for sd in (1, -1):
        sh = empty(name + "_shoulder", coll, root, (0, 0.24 * sd, z + 0.55), size=0.1)
        cyl(name + "_upperarm", 0.055, 0.32, mat_body, coll, sh, (0, 0, 0), rot=(math.pi, 0, 0))
        el = empty(name + "_elbow", coll, sh, (0, 0, -0.32), size=0.08)
        cyl(name + "_forearm", 0.05, 0.3, mat_skin, coll, el, (0, 0, 0), rot=(math.pi, 0, 0))
        arms.append((sh, el))
    return root, arms


def track_curve(name, lateral, z, coll, mat, bevel):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = bevel
    sp = cu.splines.new("POLY")
    n = 360
    sp.points.add(n - 1)
    for i in range(n):
        x, y, _ = beside(TRACK_LEN * i / n, lateral)
        sp.points[i].co = (x, y, z, 1)
    sp.use_cyclic_u = True
    ob = bpy.data.objects.new(name, cu)
    coll.objects.link(ob)
    cu.materials.append(mat)
    return ob


# =============================================================================
# 7. BINDINGS  (channel -> object property -> keyframes)
# =============================================================================
BIND = []      # (channel name or callable(t), struct, prop, index, fn)


def bind(src, struct, prop, index=0, fn=None):
    BIND.append((src, struct, prop, index, fn or (lambda v: v)))


def _fcurve(struct, prop, index):
    idd = struct.id_data
    path = struct.path_from_id(prop)
    is_arr = getattr(struct.bl_rna.properties[prop], "is_array", False)
    struct.keyframe_insert(prop, index=index if is_arr else -1, frame=1)
    if not is_arr:
        index = 0
    ad = idd.animation_data
    act = ad.action
    try:
        fc = act.fcurves.find(path, index=index)
        if fc:
            return fc
    except AttributeError:
        pass
    from bpy_extras import anim_utils
    cb = anim_utils.action_get_channelbag_for_slot(act, ad.action_slot)
    return cb.fcurves.find(path, index=index)


def write_keys(struct, prop, index, values):
    """Write one value per frame, dropping points that lie on a straight line."""
    n = len(values)
    keep = [0]
    for i in range(1, n - 1):
        if abs(values[i] - 0.5 * (values[i - 1] + values[i + 1])) > 1e-5:
            keep.append(i)
    keep.append(n - 1)
    fc = _fcurve(struct, prop, index)
    kp = fc.keyframe_points
    kp.clear()
    kp.add(len(keep))
    flat = []
    for i in keep:
        flat += [i + 1, values[i]]
    kp.foreach_set("co", flat)
    try:
        kp.foreach_set("interpolation", [1] * len(keep))     # LINEAR
    except Exception:
        for k in kp:
            k.interpolation = "LINEAR"
    fc.update()


def apply_bindings(sh):
    n = int(round(sh.duration * FPS)) + 1
    times = [f / FPS for f in range(n)]
    cache = {}
    for src, struct, prop, index, fn in BIND:
        if callable(src):
            vals = [fn(src(t)) for t in times]
        else:
            if src not in sh.ch:
                continue
            if src not in cache:
                ch = sh.ch[src]
                cache[src] = [ch.value(t) for t in times]
            vals = [fn(v) for v in cache[src]]
        write_keys(struct, prop, index, vals)


# =============================================================================
# 8. SCENE BUILD
# =============================================================================
def wipe():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    for blocks in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras,
                   bpy.data.curves, bpy.data.actions, bpy.data.particles):
        for b in list(blocks):
            blocks.remove(b)
    for t in list(bpy.data.texts):
        if t.name.startswith("SHOW_CUE_SHEET"):
            bpy.data.texts.remove(t)
    COL.clear()
    BIND.clear()


def materials():
    M = {}
    M["concrete"] = mat_pbr("Concrete", (0.25, 0.25, 0.26), 0.9)
    M["basin"] = mat_pbr("Lagoon basin", (0.04, 0.05, 0.06), 0.9)
    M["wall"] = mat_pbr("Hall wall (back)", (0.05, 0.05, 0.06), 0.9)
    M["steel"] = mat_pbr("Steel", (0.55, 0.56, 0.58), 0.35, 1.0)
    M["hydraulic"] = mat_pbr("Hydraulic rod", (0.85, 0.85, 0.88), 0.15, 1.0)
    M["mech"] = mat_pbr("Rig yellow", (0.8, 0.55, 0.05), 0.5)
    M["hull"] = mat_pbr("Boat hull", (0.85, 0.82, 0.75), 0.6)
    M["hull_red"] = mat_pbr("Boat stripe", (0.55, 0.05, 0.04), 0.6)
    M["seat"] = mat_pbr("Seat", (0.12, 0.2, 0.32), 0.7)
    M["belt"] = mat_pbr("Belt", (0.03, 0.03, 0.03), 0.8)
    M["wood"] = mat_pbr("Old wood hull", (0.3, 0.22, 0.14), 0.85)
    M["shark"] = mat_pbr("Shark", (0.22, 0.24, 0.27), 0.45)
    M["shark_belly"] = mat_pbr("Shark belly", (0.75, 0.75, 0.74), 0.5)
    M["teeth"] = mat_pbr("Teeth", (0.95, 0.93, 0.85), 0.3)
    M["black"] = mat_pbr("Black", (0.01, 0.01, 0.01), 0.3)
    M["dolphin"] = mat_pbr("Dolphin", (0.35, 0.42, 0.5), 0.35)
    M["fish"] = mat_pbr("Fish", (0.95, 0.45, 0.05), 0.4)
    M["skin"] = mat_pbr("Skin", (0.6, 0.42, 0.32), 0.6)
    M["raincoat"] = mat_pbr("Operator raincoat", (0.9, 0.7, 0.05), 0.5)
    M["crew"] = mat_pbr("Crew jacket", (0.85, 0.3, 0.05), 0.6)
    M["victim"] = mat_pbr("Victim shirt", (0.6, 0.6, 0.65), 0.7)
    M["gun"] = mat_pbr("Prop rifle", (0.08, 0.08, 0.08), 0.4, 0.6)
    M["rail"] = mat_pbr("Guard rail", (0.8, 0.8, 0.82), 0.3, 1.0)
    M["droplet"] = mat_pbr("Rain drop", (0.7, 0.8, 0.9), 0.1, 0.0, 0.6)
    M["booth"] = mat_pbr("Ticket booth", (0.35, 0.12, 0.08), 0.6)
    return M


def build_scene(sh):
    wipe()
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.render.fps = FPS
    sc.frame_end = int(round(sh.duration * FPS)) + 1
    sc.frame_start = 1
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            sc.render.engine = eng
            break
        except Exception:
            pass
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    if bpy.app.version < (5, 0, 0):
        sc.world.use_nodes = True
    bg = next((n for n in sc.world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg:
        bg.inputs["Color"].default_value = (0.004, 0.005, 0.01, 1)
        bg.inputs["Strength"].default_value = 1.0
    M = materials()
    S = sh.path.at

    C_HALL = collection("01 Hall, screen sky, sprinklers")
    C_LAG = collection("02 Lagoon, track (ramp), dock")
    C_BOAT = collection("03 Boat + 4-arm motion base")
    C_ANIM = collection("04 Animatronics (sharks, victims)")
    C_MECH = collection("05 Hidden mechanisms (pits, booms, pistons)")
    C_FX = collection("06 Lights, LEDs, water jets, rain")
    C_ENT = collection("07 Entrance: tickets, dolphin tank, fish pool")
    C_CAM = collection("08 Cameras")

    # ---------------- hall + screens -------------------------------------------
    (x0, x1), (y0, y1) = HALL_X, HALL_Y
    cxh, cyh = (x0 + x1) / 2, (y0 + y1) / 2
    W, D = x1 - x0, y1 - y0
    box("Hall floor walkway", (W, D, 0.6), (cxh, cyh, WALKWAY_Z - 0.3 - 0.01), M["concrete"], C_HALL)
    for nm, sz, lc in (("Hall wall N", (W + 1, 0.5, HALL_H), (cxh, y1 + 0.25, HALL_H / 2)),
                       ("Hall wall S", (W + 1, 0.5, HALL_H), (cxh, y0 - 0.25, HALL_H / 2)),
                       ("Hall wall E", (0.5, D, HALL_H), (x1 + 0.25, cyh, HALL_H / 2)),
                       ("Hall wall W", (0.5, D, HALL_H), (x0 - 0.25, cyh, HALL_H / 2))):
        box(nm, sz, lc, M["wall"], C_HALL)

    def stars(nodes, links):
        v = nodes.new("ShaderNodeTexVoronoi")
        v.inputs["Scale"].default_value = 900.0
        r = nodes.new("ShaderNodeValToRGB")
        r.color_ramp.elements[0].position = 0.0
        r.color_ramp.elements[0].color = (1, 1, 1, 1)
        r.color_ramp.elements[1].position = 0.045
        r.color_ramp.elements[1].color = (0, 0, 0, 1)
        links.new(v.outputs["Distance"], r.inputs["Fac"])
        return r.outputs["Color"]

    sky_m, sky_e = mat_emit("LED screen ceiling",
                            [((0.01, 0.02, 0.06), 1.0), (stars, 8.0), ((0.75, 0.85, 1.0), 0.0)],
                            (0.02, 0.03, 0.08))
    ceiling = box("Ceiling LED screens (night sky)", (W, D, 0.05), (cxh, cyh, HALL_H), sky_m, C_HALL)
    bind("sky.night", sky_e[0].inputs["Strength"], "default_value", 0, lambda v: 0.6 * v)
    bind("sky.night", sky_e[1].inputs["Strength"], "default_value", 0, lambda v: 8.0 * v)
    bind("sky.day", sky_e[2].inputs["Strength"], "default_value", 0, lambda v: 4.0 * v)

    def story(nodes, links):
        nz = nodes.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 0.08
        r = nodes.new("ShaderNodeValToRGB")
        r.color_ramp.elements[0].position = 0.45
        r.color_ramp.elements[0].color = (0, 0, 0, 1)
        r.color_ramp.elements[1].position = 0.7
        r.color_ramp.elements[1].color = (1.0, 0.45, 0.12, 1)
        links.new(nz.outputs["Fac"], r.inputs["Fac"])
        return r.outputs["Color"]

    side_m, side_e = mat_emit("LED side screens",
                              [((0.01, 0.025, 0.07), 0.4), (story, 0.0), ((0.75, 0.85, 1.0), 0.0)],
                              (0.02, 0.03, 0.08))
    for nm, sz, lc in (("Side screen N", (W - 1, 0.05, HALL_H - 1), (cxh, y1 - 0.1, HALL_H / 2 + 0.5)),
                       ("Side screen E", (0.05, D - 1, HALL_H - 1), (x1 - 0.1, cyh, HALL_H / 2 + 0.5)),
                       ("Side screen W", (0.05, D - 1, HALL_H - 1), (x0 + 0.1, cyh, HALL_H / 2 + 0.5)),
                       ("Side screen S (upper)", (W - 1, 0.05, HALL_H - 5), (cxh, y0 + 0.1, HALL_H / 2 + 2.5))):
        box(nm, sz, lc, side_m, C_HALL)
    bind("side.night", side_e[0].inputs["Strength"], "default_value", 0, lambda v: 0.4 * v)
    bind("side.story", side_e[1].inputs["Strength"], "default_value", 0, lambda v: 2.5 * v)
    bind("side.day", side_e[2].inputs["Strength"], "default_value", 0, lambda v: 3.0 * v)

    # sprinkler grid + rain particles
    for i in range(9):
        y = -LAGOON_HY + i * (2 * LAGOON_HY / 8)
        cyl("Sprinkler pipe", 0.05, 2 * LAGOON_HX, M["steel"], C_HALL, None, (-LAGOON_HX, y, HALL_H - 0.4),
            axis="X", seg=8)
    if MODE == "SHOW":
        rm, _ = mat_emit("Rain drop glint", [((0.6, 0.75, 1.0), 0.6)], (0.6, 0.7, 0.9))
        drop = sphere("Rain drop (instance source)", 0.018, rm, C_FX, None, (0, 0, -60), (1, 1, 6))
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
        bmesh.ops.scale(bm, vec=(LAGOON_HX, LAGOON_HY, 1), verts=bm.verts)
        em = obj_from_bm("Sprinkler emitter (rain)", bm, None, C_FX, None, (0, 0, HALL_H - 0.5), (math.pi, 0, 0))
        em.modifiers.new("Rain", "PARTICLE_SYSTEM")
        ps = em.particle_systems[0].settings
        r0, r1 = sh.rain
        ps.frame_start = int(r0 * FPS) + 1
        ps.frame_end = max(ps.frame_start + 1, int(r1 * FPS) + 1)
        ps.count = int(450 * (r1 - r0))
        ps.lifetime = 40
        ps.emit_from = "FACE"
        ps.normal_factor = 6.0
        ps.render_type = "OBJECT"
        ps.instance_object = drop
        ps.particle_size = 1.0
        ps.display_percentage = 40
        try:
            ps.use_rotations = True
            ps.rotation_mode = "VEL"
            ps.rotation_factor_random = 0
        except Exception:
            pass
        em.show_instancer_for_render = False
        em.show_instancer_for_viewport = False
        sh.cue(0.0, "SPRINKLERS", "rain particles: %d drops" % ps.count)

    # ---------------- lagoon + track ---------------------------------------------
    box("Lagoon floor", (2 * LAGOON_HX, 2 * LAGOON_HY, 0.4), (0, 0, -WATER_DEPTH - 0.2), M["basin"], C_LAG)
    for nm, sz, lc in (("Lagoon edge N", (2 * LAGOON_HX + 1, 0.5, WATER_DEPTH + WALKWAY_Z), (0, LAGOON_HY + 0.25, (WALKWAY_Z - WATER_DEPTH) / 2)),
                       ("Lagoon edge S", (2 * LAGOON_HX + 1, 0.5, WATER_DEPTH + WALKWAY_Z), (0, -LAGOON_HY - 0.25, (WALKWAY_Z - WATER_DEPTH) / 2)),
                       ("Lagoon edge E", (0.5, 2 * LAGOON_HY, WATER_DEPTH + WALKWAY_Z), (LAGOON_HX + 0.25, 0, (WALKWAY_Z - WATER_DEPTH) / 2)),
                       ("Lagoon edge W", (0.5, 2 * LAGOON_HY, WATER_DEPTH + WALKWAY_Z), (-LAGOON_HX - 0.25, 0, (WALKWAY_Z - WATER_DEPTH) / 2))):
        box(nm, sz, lc, M["concrete"], C_LAG)
    # the walkway slab must not cover the lagoon: cut a hole with a boolean
    hole = box("_lagoon_cutter", (2 * LAGOON_HX, 2 * LAGOON_HY, 3), (0, 0, 0), None, C_LAG)
    hole.hide_viewport = hole.hide_render = True
    floor = bpy.data.objects["Hall floor walkway"]
    bo = floor.modifiers.new("lagoon hole", "BOOLEAN")
    bo.operation = "DIFFERENCE"
    bo.object = hole
    wmat, _ = mat_water("Lagoon water", (0.02, 0.06, 0.08), 0.35 if WATER_SEE_THROUGH else 0.92)
    lagoon_w = box("Lagoon water surface", (2 * LAGOON_HX, 2 * LAGOON_HY, 0.02), (0, 0, 0), wmat, C_LAG)
    bind("lagoon.level", lagoon_w, "location", 2)
    for lat in (0.6, -0.6):
        track_curve("Ride rail %+.1f" % lat, lat, -WATER_DEPTH + 0.08, C_LAG, M["steel"], 0.08)
    track_curve("Guide slot (preprogrammed course)", 0.0, -WATER_DEPTH + 0.02, C_LAG, M["mech"], 0.04)
    # dock pier + dark boarding room (eye adaptation)
    dx, dy, _ = sh.layout["dock"]
    pier_len = abs(-LAGOON_HY - (dy + 1.5))
    box("Dock pier", (10, pier_len, 0.3), (dx, dy + 1.5 - pier_len / 2, WALKWAY_Z - 0.15), M["concrete"], C_LAG)
    for sx in (-5, 5):
        box("Dock rail guard", (0.06, pier_len, 1.1), (dx + sx, dy + 1.5 - pier_len / 2, WALKWAY_Z + 0.55), M["rail"], C_LAG)
    by0 = -LAGOON_HY - 2.0
    for nm, sz, lc in (("Boarding room wall E", (0.3, 8, 4), (dx + 6, by0 - 4, WALKWAY_Z + 2)),
                       ("Boarding room wall W", (0.3, 8, 4), (dx - 6, by0 - 4, WALKWAY_Z + 2)),
                       ("Boarding room wall S", (12, 0.3, 4), (dx, by0 - 8, WALKWAY_Z + 2)),
                       ("Boarding room roof", (12.3, 8.3, 0.3), (dx, by0 - 4, WALKWAY_Z + 4.15))):
        box(nm, sz, lc, M["wall"], C_LAG)
    for nm, lc in (("Boarding room wall N-left", dx - 4), ("Boarding room wall N-right", dx + 4)):
        box(nm, (4, 0.3, 4), (lc, by0, WALKWAY_Z + 2), M["wall"], C_LAG)
    L = bpy.data.lights.new("Boarding room (dark, eye adaptation)", "POINT")
    L.energy = 40
    L.color = (0.4, 0.5, 1.0)
    lo = bpy.data.objects.new(L.name, L)
    C_FX.objects.link(lo)
    lo.location = (dx, by0 - 4, WALKWAY_Z + 3.5)
    # UNLOAD station: separate pier + exit walkway to the nearest lagoon edge
    if "unload" in sh.layout:
        ux, uy, _ = sh.layout["unload"]
        edge = LAGOON_HY if uy > 0 else -LAGOON_HY
        ulen = abs(edge - uy) + 1.5
        box("Unload pier (guests exit)", (10, ulen, 0.3), (ux, (uy + edge) / 2, WALKWAY_Z - 0.15), M["concrete"], C_LAG)
        for sx in (-5, 5):
            box("Unload rail guard", (0.06, ulen, 1.1), (ux + sx, (uy + edge) / 2, WALKWAY_Z + 0.55), M["rail"], C_LAG)
        Lu = bpy.data.lights.new("Unload / exit light", "POINT")
        Lu.energy = 150
        o = bpy.data.objects.new(Lu.name, Lu)
        C_FX.objects.link(o)
        o.location = (ux, uy, 4.0)
    # SHOW WALLS both sides of the empty return channel (guests never see the loop close)
    def wall_strip(name, s0, s1, lat, h=4.0):
        bm = bmesh.new()
        prev = None
        k = s0
        while k <= s1 + 1e-6:
            x, y, _ = beside(k, lat)
            v0, v1 = bm.verts.new((x, y, -0.3)), bm.verts.new((x, y, h))
            if prev:
                bm.faces.new((prev[0], v0, v1, prev[1]))
            prev = (v0, v1)
            k += 1.0
        ob = obj_from_bm(name, bm, M["wall"], C_LAG)
        ob.modifiers.new("thickness", "SOLIDIFY").thickness = 0.25
        return ob
    if True:
        for lat in (3.4, -3.4):
            wall_strip("Show wall (hides empty return) %+.0f" % lat, S_UNLOAD + 9, TRACK_LEN - 9, lat)

    # ---------------- boat + carriage + 4-arm motion base ------------------------
    carriage = box("Track carriage (underwater bogie)", (2.8, 1.8, 0.3), (0, 0, 0), M["mech"], C_BOAT)
    boat = empty("BOAT (motion platform)", C_BOAT, carriage, (0, 0, -CARRIAGE_Z), size=1.0)

    def carriage_xy(i):
        return lambda t: track_point(S(t))[i]
    bind(carriage_xy(0), carriage, "location", 0)
    bind(carriage_xy(1), carriage, "location", 1)
    bind(lambda t: CARRIAGE_Z, carriage, "location", 2)
    bind(carriage_xy(2), carriage, "rotation_euler", 2)
    bind("boat.roll", boat, "rotation_euler", 0, lambda v: v * DEG)
    bind("boat.pitch", boat, "rotation_euler", 1, lambda v: -v * DEG)
    bind("boat.heave", boat, "location", 2, lambda v: -CARRIAGE_Z + v)
    hull = build_boat_hull("Boat hull", BOAT_L, BOAT_B, C_BOAT, boat, M["hull"], M["hull"])
    box("Boat stripe", (BOAT_L - 2.3, BOAT_B + 0.02, 0.12), (-1.1, 0, 0.0), M["hull_red"], C_BOAT, hull)
    # actuators: barrel aims at hull mount (Damped Track), rod stretches to it (Stretch To)
    for key, (ax, ay) in (("FL", (ARM_X, ARM_Y)), ("FR", (ARM_X, -ARM_Y)),
                          ("RL", (-ARM_X, ARM_Y)), ("RR", (-ARM_X, -ARM_Y))):
        tgt = empty("Arm %s hull mount" % key, C_BOAT, boat, (ax, ay, HULL_BOTTOM), size=0.12)
        box("Arm %s base clevis" % key, (0.3, 0.3, 0.1), (ax, ay, 0.15), M["steel"], C_BOAT, carriage)
        barrel = cyl("Arm %s cylinder" % key, 0.13, 0.42, M["mech"], C_BOAT, carriage, (ax, ay, 0.2), axis="Y")
        dtc = barrel.constraints.new("DAMPED_TRACK")
        dtc.target = tgt
        dtc.track_axis = "TRACK_Y"
        rod = cyl("Arm %s rod" % key, 0.065, 1.0, M["hydraulic"], C_BOAT, carriage, (ax, ay, 0.2), axis="Y")
        st = rod.constraints.new("STRETCH_TO")
        st.target = tgt
        st.rest_length = 1.0
        st.volume = "NO_VOLUME"
    # seats with 3-point belts + belt sensor lights
    for r in range(SEAT_ROWS):
        x = -3.0 + 0.9 * r
        for c_, y in enumerate((-1.05, -0.55, 0.55, 1.05)):
            idx = r * SEATS_PER_ROW + c_ + 1
            box("Seat %02d" % idx, (0.45, 0.45, 0.42), (x, y, 0.36), M["seat"], C_BOAT, hull)
            box("Seat %02d back" % idx, (0.08, 0.45, 0.55), (x - 0.22, y, 0.75), M["seat"], C_BOAT, hull)
            box("Seat %02d 3-pt belt" % idx, (0.02, 0.06, 0.62), (x - 0.17, y, 0.7), M["belt"], C_BOAT, hull,
                rot=(0.6, 0, 0))
            lm, le = mat_emit("Belt sensor %02d" % idx, [((0.0, 1.0, 0.1), 4.0), ((1.0, 0.0, 0.0), 0.0)])
            box("Seat %02d belt sensor LED" % idx, (0.05, 0.05, 0.05), (x - 0.22, y, 1.06), lm, C_BOAT, hull)
            bind("seat.%02d" % idx, le[0].inputs["Strength"], "default_value", 0, lambda v: 4.0 * v)
            bind("seat.%02d" % idx, le[1].inputs["Strength"], "default_value", 0, lambda v: 12.0 * (1 - v))
    # guard rails along both sides (emergency)
    for sd in (1, -1):
        cyl("Guard rail", 0.03, BOAT_L - 2.6, M["rail"], C_BOAT, hull, (-BOAT_L / 2 + 0.2, sd * (BOAT_B / 2 - 0.05), 1.15),
            axis="X", seg=8)
        for k in range(5):
            cyl("Guard rail post", 0.025, 0.3, M["rail"], C_BOAT, hull,
                (-BOAT_L / 2 + 0.3 + 1.4 * k, sd * (BOAT_B / 2 - 0.05), 0.85), seg=6)
    box("Engine housing (sound only)", (0.8, 1.4, 0.6), (-BOAT_L / 2 + 0.45, 0, 0.45), M["hull_red"], C_BOAT, hull)
    box("Helm console", (0.5, 0.7, 0.9), (2.25, 0.6, 0.6), M["hull_red"], C_BOAT, hull)
    wheel_piv = empty("Helm wheel (fake steering)", C_BOAT, hull, (2.0, 0.6, 1.15), (0, math.pi / 2 - 0.4, 0), 0.25)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=24, radius1=0.3, radius2=0.3, depth=0.04)
    obj_from_bm("Helm wheel rim", bm, M["wood"], C_BOAT, wheel_piv)
    for k in range(3):
        box("Helm wheel spoke", (0.6, 0.03, 0.03), (0, 0, 0), M["wood"], C_BOAT, wheel_piv, (0, 0, k * math.pi / 3))
    bind("boat.wheel", wheel_piv, "rotation_euler", 2, lambda v: v * DEG)
    op, op_arms = mannequin("Operator (staff)", C_BOAT, hull, (1.75, 0.6, 0.15), M["raincoat"], M["skin"])
    for sh_, el in op_arms:
        sh_.rotation_euler = (0, -1.2, 0)
    crew, crew_arms = mannequin("Crew member with prop rifle (staff)", C_BOAT, hull, (3.0, -0.5, 0.15),
                                M["crew"], M["skin"])
    for sh_, el in crew_arms:
        sh_.rotation_euler = (0, -1.5, 0)
    gun = box("Prop rifle", (1.0, 0.07, 0.1), (3.6, -0.55, 1.35), M["gun"], C_BOAT, hull)
    fl = bpy.data.lights.new("Muzzle flash strobe", "POINT")
    fl.color = (1.0, 0.75, 0.4)
    flo = bpy.data.objects.new(fl.name, fl)
    C_FX.objects.link(flo)
    flo.parent = gun
    flo.location = (0.55, 0, 0)
    bind("gun.flash", fl, "energy", 0, lambda v: 4000.0 * v)

    # ---------------- shark rigs ---------------------------------------------------
    def shark_rig(u, sled_len=0.0):
        hx, hy, yw = sh.layout[u]
        base = empty("Rig %s base" % u, C_MECH, None, (hx, hy, PIT_Z), (0, 0, yw), 0.5)
        box("Rig %s pit" % u, (BOOM_LEN + 2 + sled_len, 3.0, 1.2), (BOOM_LEN / 2, 0, 0.0), M["basin"], C_MECH, base)
        if sled_len:
            box("Rig %s sled rail" % u, (sled_len + BOOM_LEN, 0.3, 0.15), (BOOM_LEN / 2, 0, -0.1), M["steel"], C_MECH, base)
        sled = empty("Rig %s sled" % u, C_MECH, base, size=0.4)
        piv = empty("Rig %s boom hinge" % u, C_MECH, sled, size=0.4)
        cyl("Rig %s boom" % u, 0.18, BOOM_LEN, M["mech"], C_MECH, piv, axis="X", seg=10)
        cyl("Rig %s hinge ram" % u, 0.1, 1.8, M["hydraulic"], C_MECH, piv, (1.2, 0, -0.1), axis="X", seg=8)
        wrist = empty("Rig %s wrist" % u, C_MECH, piv, (BOOM_LEN, 0, 0), size=0.3)
        root = empty("SHARK %s" % u, C_ANIM, wrist, size=0.5)
        kind = SHARK_KIND.get(u, "full")
        jaw = build_shark("Shark " + u, C_ANIM, root, M, kind)
        wk = WRIST_K if kind != "head" else HEAD_WRIST_K
        bind(u + ".sled", sled, "location", 0)
        bind(u + ".boom", piv, "rotation_euler", 1, lambda v: -v * DEG)
        bind(u + ".boom", wrist, "rotation_euler", 1, lambda v, wk=wk: wk * v * DEG)
        bind(u + ".roll", wrist, "rotation_euler", 0, lambda v: v * DEG)
        if jaw:
            bind(u + ".jaw", jaw, "rotation_euler", 1, lambda v: v * DEG)
        return root

    if MODE == "SHOW":
        shark_rig("S1", sled_len=18.0)
        for u in ("P1", "P2", "P3", "P4", "S3", "S4"):
            shark_rig(u)

        # capsized boat + victim-1 (arm + top of head only)
        cx, cy, cyaw = sh.layout["capsized"]
        build_boat_hull("Capsized boat", 6.0, 2.4, C_ANIM, None, M["wood"], M["wood"],
                        (cx, cy, 0.25), (math.pi + 0.15, 0, cyaw + 0.4))
        vx, vy, vyaw = sh.layout["victim1"]
        pist = empty("Victim-1 piston", C_MECH, None, (vx, vy, -1.3), (0, 0, vyaw + math.pi / 2), 0.4)
        cyl("Victim-1 piston body", 0.12, 1.4, M["hydraulic"], C_MECH, pist, (0, 0, -1.4))
        bind("v1.lift", pist, "location", 2, lambda v: -1.3 + 1.25 * v)
        sphere("Victim-1 head (only top shows)", 0.12, M["skin"], C_ANIM, pist, (-0.25, 0, 0.95))
        shv = empty("Victim-1 shoulder", C_ANIM, pist, (0, 0, 0.85), size=0.15)
        cyl("Victim-1 upper arm", 0.06, 0.32, M["victim"], C_ANIM, shv)
        elv = empty("Victim-1 elbow", C_ANIM, shv, (0, 0, 0.32), size=0.1)
        cyl("Victim-1 forearm", 0.05, 0.3, M["skin"], C_ANIM, elv)
        sphere("Victim-1 hand", 0.07, M["skin"], C_ANIM, elv, (0, 0, 0.33))
        bind("v1.shoulder", shv, "rotation_euler", 0, lambda v: (v - 90) * DEG)
        bind("v1.elbow", elv, "rotation_euler", 0, lambda v: -v * DEG)

        # victim-2 (man who fell off a boat)
        hx, hy, hyaw = sh.layout["human2"]
        p2 = empty("Victim-2 piston", C_MECH, None, (hx, hy, -1.6), (0, 0, hyaw + math.pi / 2), 0.4)
        cyl("Victim-2 piston body", 0.15, 1.6, M["hydraulic"], C_MECH, p2, (0, 0, -1.6))
        man, arms = mannequin("Victim-2 animatronic", C_ANIM, p2, (0, 0, 0), M["victim"], M["skin"], legs=False)
        bind("h2.lift", p2, "location", 2, lambda v: -1.6 + 1.25 * v)
        for k, (sh_, el) in enumerate(arms):
            sd = 1 if k == 0 else -1
            bind("h2.wave", sh_, "rotation_euler", 0, lambda v, sd=sd: sd * (180 - v) * DEG)

        # pneumatic water jets (bullet misses)
        for g, (gx, gy, _) in sh.layout["geysers"].items():
            gp = empty("Water jet %s nozzle" % g, C_FX, None, (gx, gy, -0.2), size=0.3)
            cyl("Water jet %s plume" % g, 0.35, 2.2, M["droplet"], C_FX, gp, r2=0.05, seg=10)
            gp.scale = (1, 1, 0.001)
            bind(g + ".jet", gp, "scale", 2, lambda v: max(0.001, v))
            bind(g + ".jet", gp, "scale", 0, lambda v: max(0.001, v))
            bind(g + ".jet", gp, "scale", 1, lambda v: max(0.001, v))

    # ---------------- lights ------------------------------------------------------
    def sun(name, color, chan, k, rot):
        Ld = bpy.data.lights.new(name, "SUN")
        Ld.color = color
        Ld.angle = 0.5
        o = bpy.data.objects.new(name, Ld)
        C_FX.objects.link(o)
        o.rotation_euler = rot
        o.location = (0, 0, HALL_H + 2)
        bind(chan, Ld, "energy", 0, lambda v: k * v)
        Ld.energy = k * (sh.ch[chan].rest if chan in sh.ch else 0)
        return Ld
    sun("Screen sky fill: night (moonlight)", (0.55, 0.65, 1.0), "sky.night", 0.06, (0.3, 0.2, 0))
    sun("Screen sky fill: DAYLIGHT", (1.0, 0.97, 0.92), "sky.day", 2.5, (0.2, -0.3, 0))
    sun("House work lights (E-STOP / maintenance)", (1.0, 0.95, 0.85), "house.lights", 1.5, (0.05, 0.05, 0))
    if VIEW == "ENGINEERING":
        Lf = bpy.data.lights.new("Engineering view fill (not part of the show)", "SUN")
        Lf.energy = 0.35
        Lf.angle = 1.0
        o = bpy.data.objects.new(Lf.name, Lf)
        C_FX.objects.link(o)
        o.rotation_euler = (0.6, 0.3, 0.8)
    for name, (x, y) in sh.layout.get("spots", {}).items():
        Ls = bpy.data.lights.new("Scene spot: " + name, "SPOT")
        Ls.color = (0.6, 0.75, 1.0)
        Ls.spot_size = 40 * DEG
        Ls.spot_blend = 0.6
        o = bpy.data.objects.new(Ls.name, Ls)
        C_FX.objects.link(o)
        o.location = (x, y, HALL_H - 1.0)
        bind("spot." + name, Ls, "energy", 0, lambda v: 9000.0 * v)

    # ---------------- entrance: tickets, dolphin tank, fish pool --------------------
    tx, ty = TICKET_C
    box("Ticket booth", (6, 3, 3), (tx, ty, 1.5), M["booth"], C_ENT)
    box("Ticket booth roof", (7, 4, 0.3), (tx, ty, 3.15), M["wall"], C_ENT)
    box("Entrance plaza", (100, 12, 0.2), (0, HALL_Y[0] - 6, -0.1), M["concrete"], C_ENT)
    dcx, dcy = DOLPHIN_TANK_C
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=64, radius1=DOLPHIN_TANK_R, radius2=DOLPHIN_TANK_R,
                          depth=DOLPHIN_TANK_DEPTH + DOLPHIN_RIM)
    tank = obj_from_bm("Dolphin tank wall", bm, M["concrete"], C_ENT, None,
                       (dcx, dcy, (DOLPHIN_RIM - DOLPHIN_TANK_DEPTH) / 2))
    tank.modifiers.new("wall", "SOLIDIFY").thickness = 0.6
    cyl("Dolphin tank floor", DOLPHIN_TANK_R, 0.3, M["basin"], C_ENT, None, (dcx, dcy, -DOLPHIN_TANK_DEPTH - 0.3), seg=64)
    dmat, dsock = mat_water("Dolphin tank water", (0.02, 0.07, 0.1), 0.4 if WATER_SEE_THROUGH else 0.9, True)
    dw = cyl("Dolphin tank water surface", DOLPHIN_TANK_R - 0.05, 0.02, dmat, C_ENT, None, (dcx, dcy, 0), seg=64)
    bind("dtank.level", dw, "location", 2)
    bind("led.red", dsock, "default_value", 0, lambda v: 1.2 * v)
    for k in range(10):
        a = 2 * math.pi * k / 10
        Ld = bpy.data.lights.new("Red underwater LED %d" % (k + 1), "POINT")
        Ld.color = (1.0, 0.0, 0.0)
        Ld.shadow_soft_size = 0.5
        o = bpy.data.objects.new(Ld.name, Ld)
        C_FX.objects.link(o)
        o.location = (dcx + 0.8 * DOLPHIN_TANK_R * math.cos(a), dcy + 0.8 * DOLPHIN_TANK_R * math.sin(a), -1.0)
        bind("led.red", Ld, "energy", 0, lambda v: 3000.0 * v)
    for k in range(4):
        a = 2 * math.pi * k / 4 + 0.4
        Ld = bpy.data.lights.new("Dolphin show spot %d" % (k + 1), "SPOT")
        Ld.spot_size = 0.9
        o = bpy.data.objects.new(Ld.name, Ld)
        C_FX.objects.link(o)
        o.location = (dcx + 30 * math.cos(a), dcy + 30 * math.sin(a), 14)
        tc = o.constraints.new("TRACK_TO")
        tc.target = empty("Dolphin tank centre", C_ENT, None, (dcx, dcy, 0))
        tc.track_axis = "TRACK_NEGATIVE_Z"
        tc.up_axis = "UP_Y"
        bind("dshow.lights", Ld, "energy", 0, lambda v: 60000.0 * v)
    # light sign
    cu = bpy.data.curves.new("Light sign", "FONT")
    cu.body = SIGN_TEXT
    cu.size = 4.0
    cu.extrude = 0.15
    cu.align_x = "CENTER"
    sgn = bpy.data.objects.new("Light sign (edit text: SIGN_TEXT)", cu)
    C_ENT.objects.link(sgn)
    sgn.location = (dcx, dcy - DOLPHIN_TANK_R - 4, DOLPHIN_RIM + 3)
    sgn.rotation_euler = (math.pi / 2, 0, math.pi)
    sm, se = mat_emit("Light sign", [((1.0, 0.92, 0.75), 0.0)], (0.9, 0.8, 0.6))
    cu.materials.append(sm)
    bind("sign", se[0].inputs["Strength"], "default_value", 0, lambda v: 12.0 * v)
    box("Light sign frame", (len(SIGN_TEXT) * 2.6 + 2, 0.4, 6), (dcx, dcy - DOLPHIN_TANK_R - 4.4, DOLPHIN_RIM + 4),
        M["wall"], C_ENT)
    # dolphin rigs
    for k in range(3):
        u = "dol%d" % (k + 1)
        hx, hy, yw = sh.layout.get(u, (dcx + 7 * math.cos(k), dcy + 7 * math.sin(k), k))
        base = empty("Dolphin rig %d hinge" % (k + 1), C_MECH, None, (hx, hy, DOLPHIN_PIT_Z), (0, 0, yw), 0.4)
        piv = empty("Dolphin rig %d boom" % (k + 1), C_MECH, base, size=0.3)
        cyl("Dolphin rig %d arm" % (k + 1), 0.1, DOLPHIN_BOOM, M["mech"], C_MECH, piv, axis="X", seg=8)
        build_dolphin("DOLPHIN %d" % (k + 1), C_ANIM, empty("Dolphin %d mount" % (k + 1), C_MECH, piv,
                                                            (DOLPHIN_BOOM, 0, 0), (0, math.pi / 2, 0), 0.2),
                      M["dolphin"])
        bind(u + ".phi", piv, "rotation_euler", 1, lambda v: -v * DEG)
    if "DS" in sh.layout:
        hx, hy, yw = sh.layout["DS"]
        base = empty("Rig DS base", C_MECH, None, (hx, hy, PIT_Z), (0, 0, yw), 0.5)
        piv = empty("Rig DS boom hinge", C_MECH, base, size=0.4)
        cyl("Rig DS boom", 0.18, BOOM_LEN, M["mech"], C_MECH, piv, axis="X", seg=10)
        wrist = empty("Rig DS wrist", C_MECH, piv, (BOOM_LEN, 0, 0), size=0.3)
        jaw = build_shark("Shark DS", C_ANIM, empty("SHARK DS", C_ANIM, wrist, size=0.5), M, SHARK_KIND["DS"])
        bind("DS.boom", piv, "rotation_euler", 1, lambda v: -v * DEG)
        bind("DS.boom", wrist, "rotation_euler", 1, lambda v: HEAD_WRIST_K * v * DEG)
        bind("DS.jaw", jaw, "rotation_euler", 1, lambda v: v * DEG)
    # transfer pump + pipe lagoon <-> dolphin tank (night drain / day refill)
    px, py = (dcx + 10, HALL_Y[0] - 3)
    box("Transfer pump house", (4, 3, 3), (px, py, 1.5), M["wall"], C_ENT)
    imp = empty("Transfer pump impeller", C_ENT, None, (px, py, 3.4), size=0.5)
    for k in range(4):
        box("Impeller blade", (1.6, 0.15, 0.4), (0, 0, 0), M["steel"], C_ENT, imp, (0, 0, k * math.pi / 4))
    bind("pump.rot", imp, "rotation_euler", 2, lambda v: v * DEG)
    cyl("Transfer pipe (to lagoon)", 0.5, abs(py - (-LAGOON_HY)) + 2, M["steel"], C_ENT, None,
        (px, py, -1.0), axis="Y", seg=12)
    cyl("Transfer pipe (to dolphin tank)", 0.5, abs(dcy + DOLPHIN_TANK_R - py) + 1, M["steel"], C_ENT, None,
        (px, dcy + DOLPHIN_TANK_R - 1, -1.0), axis="Y", seg=12)
    # fish pool (real fish - placeholder swim motion, not show-controlled)
    fx, fy = FISH_POOL_C
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=40, radius1=FISH_POOL_R, radius2=FISH_POOL_R, depth=2.0)
    fp = obj_from_bm("Fish pool wall", bm, M["concrete"], C_ENT, None, (fx, fy, -0.5))
    fp.modifiers.new("wall", "SOLIDIFY").thickness = 0.3
    fmat, _ = mat_water("Fish pool water", (0.05, 0.15, 0.15), 0.35)
    cyl("Fish pool water", FISH_POOL_R - 0.05, 0.02, fmat, C_ENT, None, (fx, fy, 0.2), seg=40)
    spin = empty("Fish school (real fish, placeholder motion)", C_ENT, None, (fx, fy, -0.3))
    for k in range(10):
        a = 2 * math.pi * k / 10
        rr = 1.5 + 2.0 * ((k * 7) % 5) / 5
        fo = build_dolphin("Fish", C_ENT, spin, M["fish"], 0.35)
        fo.location = (rr * math.cos(a), rr * math.sin(a), -0.2 * (k % 3))
        fo.rotation_euler = (0, 0, a + math.pi / 2)
    bind(lambda t: 0.25 * t, spin, "rotation_euler", 2)

    # ---------------- cameras ------------------------------------------------------
    def camera(name, loc, parent=None, target=None, lens=24, rot=None):
        cd = bpy.data.cameras.new(name)
        cd.lens = lens
        cd.clip_end = 600
        o = bpy.data.objects.new(name, cd)
        C_CAM.objects.link(o)
        o.parent = parent
        o.location = loc
        if rot:
            o.rotation_euler = rot
        if target:
            tc = o.constraints.new("TRACK_TO")
            tc.target = target
            tc.track_axis = "TRACK_NEGATIVE_Z"
            tc.up_axis = "UP_Y"
        return o
    rider = camera("CAM rider POV (back row)", (-3.4, 0.0, 1.8), hull, lens=14, rot=(math.radians(84), 0, -math.pi / 2))
    camera("CAM chase (right-rear, follows boat)", (-11.0, -8.0, 4.0), carriage,
           empty("Chase target", C_CAM, carriage, (3.0, -2.5, 1.0)), lens=20)
    camera("CAM overview (hall corner)", (-44, -33, 11.5), None,
           empty("Overview target", C_CAM, None, (5, 3, -1)), lens=18)
    camera("CAM motion base (underwater)", (0.5, -4.5, 0.0), carriage, carriage, lens=24)
    if VIEW == "ENGINEERING":
        Lw = bpy.data.lights.new("Engineering work light on carriage", "POINT")
        Lw.energy = 600
        o = bpy.data.objects.new(Lw.name, Lw)
        C_FX.objects.link(o)
        o.parent = carriage
        o.location = (1.0, -3.0, 0.3)
    camera("CAM dolphin night show", (dcx + 22, dcy + 30, 14), None,
           empty("Dolphin cam target", C_CAM, None, (dcx, dcy, 0)), lens=30)
    sc.camera = rider

    # ---------------- markers + cue sheet ---------------------------------------------
    sc.timeline_markers.clear()
    used = set()
    for t, text in sh.markers:
        f = int(round(t * FPS)) + 1
        while f in used:
            f += 1
        used.add(f)
        sc.timeline_markers.new(text[:63], frame=f)
    txt = bpy.data.texts.new("SHOW_CUE_SHEET.csv")
    txt.write(cue_sheet_csv(sh))
    rep = bpy.data.texts.new("SHOW_CUE_SHEET_actuator_report.txt")
    rep.write(actuator_report(sh))

    apply_bindings(sh)
    sc.frame_set(1)
    if AUTO_RIDER_VIEW:
        rider_view()


def rider_view():
    """Jump to the Layout tab and look through the rider camera, lights on."""
    try:
        win = bpy.context.window
        ws = bpy.data.workspaces.get("Layout")
        screens = [win.screen]
        if ws:
            win.workspace = ws
            screens = list(ws.screens)
        for scr in screens:
            for area in scr.areas:
                if area.type == "VIEW_3D":
                    sp = area.spaces.active
                    sp.clip_end = 1000
                    sp.shading.type = "RENDERED"
                    sp.region_3d.view_perspective = "CAMERA"
        print("Rider view ready: hover over the 3D view and press Space to ride.")
    except Exception as e:
        print("Could not switch view automatically:", e)


# =============================================================================
# RUN
# =============================================================================
def main():
    sh = build_program()
    print(actuator_report(sh))
    if bpy is None:
        with open("jaws_show_cue_sheet.csv", "w") as f:
            f.write(cue_sheet_csv(sh))
        print("bpy not available: wrote jaws_show_cue_sheet.csv only.")
        return
    build_scene(sh)
    print("JAWS ride built: %d channels, %d cues, %.1f s (%d frames)."
          % (len(sh.ch), len(sh.cues), sh.duration, bpy.context.scene.frame_end))


if __name__ == "__main__":
    main()
